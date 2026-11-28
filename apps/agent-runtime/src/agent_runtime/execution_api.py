"""Public composition facade for background worker execution (DEC-07 D7-02 / DATA-07).

This is the only runtime module jobs-worker is permitted to import.
Internal repositories, routers, LLM internals, and Control API are strictly encapsulated.
"""

from __future__ import annotations

import logging

from platform_common.execution import (
    AsyncRunExecutor,
    ExecutionCommand,
    ExecutionDisposition,
    ExecutionDispositionType,
    LeaseIdentity,
)

from agent_runtime.application.execution.grant import (
    ExecutionGrant,
    normalize_encryption_key,
)
from agent_runtime.application.execution.state_owner import ExecutionStateOwner
from agent_runtime.persistence.execution_repository import (
    ClaimedJobRecord,
    ExecutionRepository,
    LostLeaseError,
    RunAlreadyTerminalError,
)
from agent_runtime.settings import RuntimeSettings, get_settings

logger = logging.getLogger(__name__)


def validate_execution_configuration(settings: RuntimeSettings | None = None) -> None:
    """Fail-closed configuration check. If execution flag is enabled, required keys/configs must be present."""
    s = settings or get_settings()
    if s.run_execution_grants_enabled:
        if not s.run_execution_key:
            raise ValueError(
                "run_execution_grants_enabled=True requires RUN_EXECUTION_KEY to be configured"
            )
        normalize_encryption_key(s.run_execution_key)
    if s.run_worker_execution_enabled:
        if not s.database_url:
            raise ValueError(
                "run_worker_execution_enabled=True requires DATABASE_URL to be configured"
            )
        if s.run_execution_grants_enabled and not s.run_execution_key:
            raise ValueError("Worker execution with grants enabled requires RUN_EXECUTION_KEY")


class SentinelRunExecutor:
    """Sentinel executor proving async port, lease fencing, and state owner transitions (07-A).

    Proves the full worker->runtime port, lease fencing, heartbeat, and state ownership
    without claiming real LangGraph generation (which is Step 07-B).
    """

    def __init__(
        self,
        state_owner: ExecutionStateOwner,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self._state_owner = state_owner
        self._settings = settings or get_settings()

    async def execute(self, command: ExecutionCommand) -> ExecutionDisposition:
        """Execute run lifecycle transition under active lease fencing. None is never success."""
        # 1. Begin execution: transition queued -> running, pin started/deadline, re-verify grant
        begin_res, _grant, _principal = await self._state_owner.begin_execution(
            lease_identity=command.lease_identity,
            max_wall_time_seconds=self._settings.run_execution_max_wall_time_seconds,
            require_grant=self._settings.run_execution_grants_enabled,
        )

        if not begin_res.success:
            if begin_res.timed_out:
                return ExecutionDisposition(
                    disposition=ExecutionDispositionType.BLOCKED_UNKNOWN,
                    lease_identity=command.lease_identity,
                    error_code=begin_res.error_code or "execution_deadline_exceeded",
                    message="Execution deadline exceeded during begin",
                    terminal=True,
                )
            return ExecutionDisposition(
                disposition=ExecutionDispositionType.DENIED,
                lease_identity=command.lease_identity,
                error_code=begin_res.error_code or "execution_authority_denied",
                message="Execution authority denied during begin",
                terminal=True,
            )

        # 2. Complete execution: transition running -> succeeded, job -> done under lease fence
        return await self._state_owner.complete_execution(
            lease_identity=command.lease_identity,
        )


def create_execution_state_owner(
    settings: RuntimeSettings | None = None,
) -> ExecutionStateOwner:
    """Factory creating the authoritative ExecutionStateOwner."""
    active_settings = settings or get_settings()
    validate_execution_configuration(active_settings)
    return ExecutionStateOwner(settings=active_settings)


def create_sentinel_executor(
    state_owner: ExecutionStateOwner | None = None,
    settings: RuntimeSettings | None = None,
) -> AsyncRunExecutor:
    """Factory creating the SentinelRunExecutor for 07-A verification."""
    active_settings = settings or get_settings()
    owner = state_owner or create_execution_state_owner(active_settings)
    return SentinelRunExecutor(state_owner=owner, settings=active_settings)


__all__ = [
    "AsyncRunExecutor",
    "ClaimedJobRecord",
    "ExecutionCommand",
    "ExecutionDisposition",
    "ExecutionDispositionType",
    "ExecutionGrant",
    "ExecutionRepository",
    "ExecutionStateOwner",
    "LeaseIdentity",
    "LostLeaseError",
    "RunAlreadyTerminalError",
    "SentinelRunExecutor",
    "create_execution_state_owner",
    "create_sentinel_executor",
    "validate_execution_configuration",
]
