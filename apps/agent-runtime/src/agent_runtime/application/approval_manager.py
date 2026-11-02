"""
Human-in-the-Loop (HITL) Approval Service.
Enforces Compare-And-Swap (CAS) state transitions, action hash validation,
TTL expiration, and protection against double-approval or replay attacks.
"""

import enum
import hashlib
import json
import time
import uuid
from typing import Dict, Any, Optional


class ApprovalStatus(enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalManager:
    def __init__(self, default_ttl_seconds: float = 3600.0):
        self.default_ttl_seconds = default_ttl_seconds
        self.approvals: Dict[str, Dict[str, Any]] = {}

    def _compute_action_hash(self, tool_id: str, arguments: Dict[str, Any]) -> str:
        raw = f"{tool_id}:{json.dumps(arguments, sort_keys=True)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create_approval_request(
        self,
        run_id: str,
        workspace_id: str,
        tool_id: str,
        arguments: Dict[str, Any],
        approver_role: str = "approver"
    ) -> str:
        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        action_hash = self._compute_action_hash(tool_id, arguments)
        now = time.time()

        self.approvals[approval_id] = {
            "approval_id": approval_id,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "tool_id": tool_id,
            "arguments": arguments,
            "action_hash": action_hash,
            "approver_role": approver_role,
            "status": ApprovalStatus.PENDING,
            "created_at": now,
            "expires_at": now + self.default_ttl_seconds,
            "decided_by": None,
            "decided_at": None
        }
        return approval_id

    def decide(
        self,
        approval_id: str,
        decision: str,  # "approve" or "reject"
        actor_id: str,
        action_hash_verification: str
    ) -> Dict[str, Any]:
        """
        Executes atomic CAS transition.
        Verifies action hash has not mutated and approval has not expired.
        """
        if approval_id not in self.approvals:
            raise ValueError(f"Approval request '{approval_id}' not found.")

        record = self.approvals[approval_id]

        # Check atomic CAS
        if record["status"] != ApprovalStatus.PENDING:
            raise ValueError(f"Approval '{approval_id}' is already finalized as {record['status'].value}")

        # Check expiration
        if time.time() > record["expires_at"]:
            record["status"] = ApprovalStatus.EXPIRED
            raise ValueError(f"Approval request '{approval_id}' has expired.")

        # Check action hash tamper protection
        if record["action_hash"] != action_hash_verification:
            raise ValueError("Action hash mismatch. The proposed arguments have been modified.")

        # Update status
        record["status"] = ApprovalStatus.APPROVED if decision.lower() == "approve" else ApprovalStatus.REJECTED
        record["decided_by"] = actor_id
        record["decided_at"] = time.time()

        return record

