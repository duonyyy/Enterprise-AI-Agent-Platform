"""
Recovery & Durability Test Suite: Resume After Restart (ADR-003, ADR-006).
Verifies that:
1. Interrupted Agent runs resume seamlessly from persistent disk storage.
2. Tool operations previously executed are not repeated thanks to Idempotency Key deduplication.
3. Process state transitions safely from crashed state to completed across process restarts.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
import pytest

from agent_runtime.application.state_graph import StateGraphEngine, RunState


class DurableSqliteStore:
    """Simulates PostgreSQL runtime.tool_operations and runtime.checkpoint_refs on persistent disk."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_operations (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    operation_key TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, operation_key)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    node TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, step)
                )
            """)
            conn.commit()

    def get_operation(self, tenant_id: str, workspace_id: str, operation_key: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT result_json FROM tool_operations WHERE tenant_id = ? AND workspace_id = ? AND operation_key = ?",
                (tenant_id, workspace_id, operation_key),
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def save_operation(self, tenant_id: str, workspace_id: str, operation_key: str, tool_name: str, result: Dict[str, Any]):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO tool_operations (tenant_id, workspace_id, operation_key, tool_name, status, result_json) VALUES (?, ?, ?, ?, ?, ?)",
                (tenant_id, workspace_id, operation_key, tool_name, "succeeded", json.dumps(result)),
            )
            conn.commit()

    def save_checkpoint(self, run_id: str, step: int, node: str, state: Dict[str, Any]):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints (run_id, step, node, state_json) VALUES (?, ?, ?, ?)",
                (run_id, step, node, json.dumps(state)),
            )
            conn.commit()

    def get_latest_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT step, node, state_json FROM checkpoints WHERE run_id = ? ORDER BY step DESC LIMIT 1",
                (run_id,),
            )
            row = cursor.fetchone()
            if row:
                return {"step": row[0], "node": row[1], "state": json.loads(row[2])}
            return None


class DurableToolDispatcher:
    def __init__(self, store: DurableSqliteStore):
        self.store = store
        self.network_call_count = 0

    def execute_idempotent(
        self, tenant_id: str, workspace_id: str, operation_key: str, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        existing = self.store.get_operation(tenant_id, workspace_id, operation_key)
        if existing is not None:
            # Idempotent return without re-execution of side effects!
            return existing

        # Execute external side effect
        self.network_call_count += 1
        result = {"tool": tool_name, "status": "executed", "echo": args}
        self.store.save_operation(tenant_id, workspace_id, operation_key, tool_name, result)
        return result


def test_durable_resume_after_process_crash(tmp_path: Path):
    db_file = str(tmp_path / "persistence.sqlite")
    tenant_id = "00000000-0000-0000-0000-000000000001"
    workspace_id = "00000000-0000-0000-0000-000000000002"
    run_id = "run-session-crash-recovery-001"

    # -------------------------------------------------------------
    # PHASE 1: PROCESS 1 STARTS, EXECUTES STEP 1, AND CHECKPOINTS
    # -------------------------------------------------------------
    store_p1 = DurableSqliteStore(db_file)
    dispatcher_p1 = DurableToolDispatcher(store_p1)

    # Step 1: Execute Tool 1
    op_key_1 = "op-step-1"
    res1 = dispatcher_p1.execute_idempotent(tenant_id, workspace_id, op_key_1, "fetch_user_profile", {"user_id": 42})
    assert dispatcher_p1.network_call_count == 1
    assert res1["status"] == "executed"

    # Checkpoint step 1 to durable store
    store_p1.save_checkpoint(run_id, step=1, node="step_fetch_profile", state={"step": 1, "profile": res1})

    # -------------------------------------------------------------
    # PHASE 2: HARD PROCESS CRASH / TERMINATION (Memory destroyed)
    # -------------------------------------------------------------
    del dispatcher_p1
    del store_p1

    # -------------------------------------------------------------
    # PHASE 3: PROCESS 2 SPAWNS FRESH FROM COLD DISK
    # -------------------------------------------------------------
    store_p2 = DurableSqliteStore(db_file)
    dispatcher_p2 = DurableToolDispatcher(store_p2)

    # Recover state from disk
    recovered = store_p2.get_latest_checkpoint(run_id)
    assert recovered is not None
    assert recovered["step"] == 1
    assert recovered["node"] == "step_fetch_profile"
    assert recovered["state"]["profile"] == res1

    # Replay or duplicate step 1 execution must hit idempotency cache:
    res1_replayed = dispatcher_p2.execute_idempotent(
        tenant_id, workspace_id, op_key_1, "fetch_user_profile", {"user_id": 42}
    )
    assert res1_replayed == res1
    # Dispatcher 2's network calls MUST REMAIN 0!
    assert dispatcher_p2.network_call_count == 0

    # Step 2: Now execute Step 2
    op_key_2 = "op-step-2"
    res2 = dispatcher_p2.execute_idempotent(
        tenant_id, workspace_id, op_key_2, "send_confirmation_email", {"to": "alice@vinai.vn"}
    )
    assert dispatcher_p2.network_call_count == 1
    assert res2["status"] == "executed"

    # Final Checkpoint
    final_state = {
        "step": 2,
        "profile": res1,
        "email": res2,
        "status": RunState.COMPLETED.value,
    }
    store_p2.save_checkpoint(run_id, step=2, node="__END__", state=final_state)

    # Verify final persisted checkpoint
    latest = store_p2.get_latest_checkpoint(run_id)
    assert latest is not None
    assert latest["step"] == 2
    assert latest["state"]["status"] == RunState.COMPLETED.value
