"""
LangGraph-Style State Graph Engine with Persistent Checkpointing.
Enforces deterministic state transitions, interruptible workflows, and durable state resumption.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any, Callable, Dict, Optional, Tuple


class RunState(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StateGraphEngine:
    """
    Durable workflow state machine.
    A production engine is deliberately unusable without a durable LangGraph-compatible
    checkpointer. Keeping an in-process fallback would turn a restart into a silent loss
    of execution state and can replay side effects.
    """

    def __init__(self, checkpointer: Any):
        if checkpointer is None:
            raise ValueError("A durable checkpointer is required for StateGraphEngine")
        self.nodes: Dict[str, Callable] = {}
        self.edges: Dict[str, str] = {}
        self.conditional_edges: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self.checkpointer = checkpointer

    def add_node(self, name: str, func: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self.nodes[name] = func

    def add_edge(self, from_node: str, to_node: str) -> None:
        self.edges[from_node] = to_node

    def add_conditional_edge(self, from_node: str, router: Callable[[Dict[str, Any]], str]) -> None:
        self.conditional_edges[from_node] = router

    def checkpoint(
        self,
        run_id: str,
        state_data: Dict[str, Any],
        current_node: str,
        step: int = 0,
    ) -> str:
        checkpoint_id = f"chk-{uuid.uuid4().hex[:12]}"
        record = {
            "checkpoint_id": checkpoint_id,
            "node": current_node,
            "step": step,
            "state": dict(state_data),
        }

        config = {"configurable": {"thread_id": run_id, "checkpoint_id": checkpoint_id}}
        try:
            self.checkpointer.put(config, record, {})
        except Exception as exc:
            raise RuntimeError("Durable checkpoint write failed; run must not continue") from exc
        return checkpoint_id

    def get_latest_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve state only from the configured durable checkpointer."""
        try:
            config = {"configurable": {"thread_id": run_id}}
            tuple_res = self.checkpointer.get_tuple(config)
        except Exception as exc:
            raise RuntimeError("Durable checkpoint read failed; refusing memory fallback") from exc
        if tuple_res and hasattr(tuple_res, "checkpoint") and tuple_res.checkpoint:
            return tuple_res.checkpoint
        return None

    def run_step(
        self,
        run_id: str,
        current_node: str,
        state_data: Dict[str, Any],
        step: int = 0,
    ) -> Tuple[str, Dict[str, Any], RunState]:
        """
        Executes a single step in the state machine.
        If node signals `requires_approval`, pauses state and enters WAITING_APPROVAL.
        """
        if current_node not in self.nodes:
            return current_node, state_data, RunState.FAILED

        # Execute node
        node_func = self.nodes[current_node]
        output_state = node_func(state_data)

        # Checkpoint state immediately
        self.checkpoint(run_id, output_state, current_node, step=step)

        # Check for interrupt / approval requirement
        if output_state.get("__requires_approval__"):
            return current_node, output_state, RunState.WAITING_APPROVAL

        # Determine next node
        if current_node in self.conditional_edges:
            next_node = self.conditional_edges[current_node](output_state)
        else:
            next_node = self.edges.get(current_node, "__END__")

        status = RunState.COMPLETED if next_node == "__END__" else RunState.RUNNING
        return next_node, output_state, status
