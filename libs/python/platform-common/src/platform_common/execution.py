"""Pure async execution port and typed data structures for worker-runtime execution (DEC-07 / DATA-07).

Deliberately free of framework dependencies (no FastAPI, SQLAlchemy, or LangGraph imports).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol


class ExecutionDispositionType(enum.StrEnum):
    """Canonical typed execution dispositions returned by RunExecutor (DEC-07 D7-02)."""

    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    BLOCKED_UNKNOWN = "blocked_unknown"
    LOST_LEASE = "lost_lease"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class LeaseIdentity:
    """Fenced identity of a leased job claim (DEC-07 D7-04)."""

    tenant_id: str
    workspace_id: str
    job_id: str
    run_id: str | None
    leased_by: str
    attempt: int
    lease_token: str


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    """Command dispatched to the run executor under an active fenced lease."""

    lease_identity: LeaseIdentity
    payload_ref: str


@dataclass(frozen=True, slots=True)
class ExecutionDisposition:
    """Typed outcome returned by the run executor. None is never success."""

    disposition: ExecutionDispositionType
    lease_identity: LeaseIdentity
    error_code: str | None = None
    message: str | None = None
    retry_delay_seconds: float | None = None
    terminal: bool = False


class AsyncRunExecutor(Protocol):
    """Pure async executor protocol implemented by runtime and invoked by worker."""

    async def execute(self, command: ExecutionCommand) -> ExecutionDisposition:
        """Execute the pinned run and return a typed disposition. None is never success."""
        ...
