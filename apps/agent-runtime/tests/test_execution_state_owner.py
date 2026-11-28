"""Unit tests for ExecutionStateOwner (DEC-07 D7-02 / D7-04 / D7-06)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from agent_runtime.application.execution.grant import (
    DEFAULT_KEY_ID,
    ExecutionGrant,
    encrypt_execution_grant,
)
from agent_runtime.application.execution.state_owner import ExecutionStateOwner
from agent_runtime.application.run_admission import OutboxPublisherPort
from agent_runtime.application.run_events import RunEventDTO
from agent_runtime.auth import ParentSessionVerifierProtocol, RuntimePrincipal
from agent_runtime.persistence.execution_repository import (
    BeginExecutionResult,
    ClaimedJobRecord,
    ExecutionRepository,
)
from agent_runtime.settings import RuntimeSettings
from platform_common.execution import (
    ExecutionDispositionType,
    LeaseIdentity,
)


class _FakeOutboxPublisher(OutboxPublisherPort):
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish_in_transaction(
        self,
        session: Any,
        topic: str,
        partition_key: str,
        payload: dict[str, Any],
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        event_id: str | None = None,
        set_rls: bool = True,
    ) -> None:
        self.published.append(
            {
                "topic": topic,
                "partition_key": partition_key,
                "payload": payload,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_id": event_id,
            }
        )


class _FakeSessionVerifier(ParentSessionVerifierProtocol):
    def __init__(self, status: str = "OK") -> None:
        self.status = status

    def verify_parent_session(
        self, *, session_id: str, user_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        if self.status == "OK":
            return "OK", {"status": "active", "user_id": user_id}
        return self.status, None


class _FakeRepo(ExecutionRepository):
    def __init__(self) -> None:
        self.begin_result = BeginExecutionResult(
            success=True,
            started=True,
        )
        self.extend_result = True
        self.reclaim_count = 0
        self.calls: list[str] = []

    async def claim_next_job(self, session: Any, **kwargs: Any) -> ClaimedJobRecord | None:
        self.calls.append("claim_next_job")
        return None

    async def extend_lease(self, session: Any, **kwargs: Any) -> bool:
        self.calls.append("extend_lease")
        return self.extend_result

    async def begin_execution(self, session: Any, **kwargs: Any) -> BeginExecutionResult:
        self.calls.append("begin_execution")
        return self.begin_result

    async def complete_execution(self, session: Any, **kwargs: Any) -> RunEventDTO:
        self.calls.append("complete_execution")
        return RunEventDTO(
            eventId=str(uuid4()),
            type="run.completed",
            workspaceId=kwargs["workspace_id"],
            runId=kwargs["run_id"],
            sequence=2,
            timestamp=datetime.now(UTC).isoformat(),
            data={"status": "completed"},
        )

    async def fail_execution(self, session: Any, **kwargs: Any) -> RunEventDTO:
        self.calls.append("fail_execution")
        return RunEventDTO(
            eventId=str(uuid4()),
            type="run.failed",
            workspaceId=kwargs["workspace_id"],
            runId=kwargs["run_id"],
            sequence=2,
            timestamp=datetime.now(UTC).isoformat(),
            data={"status": "failed", "error_code": kwargs.get("error_code")},
        )

    async def schedule_retry(self, session: Any, **kwargs: Any) -> RunEventDTO | None:
        self.calls.append("schedule_retry")
        return None

    async def reclaim_expired_leases(self, session: Any, **kwargs: Any) -> int:
        self.calls.append("reclaim_expired_leases")
        return self.reclaim_count


@asynccontextmanager
async def _fake_session_factory(t_id: str, w_id: str, role: str | None = None):
    yield object()


def _build_state_owner(
    repo: _FakeRepo | None = None,
    outbox: _FakeOutboxPublisher | None = None,
    verifier: _FakeSessionVerifier | None = None,
    key: bytes | None = None,
) -> ExecutionStateOwner:
    key_bytes = key or os.urandom(32)
    settings = RuntimeSettings(
        run_execution_key=key_bytes.hex(),
        run_execution_key_id=DEFAULT_KEY_ID,
    )
    return ExecutionStateOwner(
        session_factory=_fake_session_factory,
        repository=repo or _FakeRepo(),
        outbox_publisher=outbox or _FakeOutboxPublisher(),
        parent_session_verifier=verifier or _FakeSessionVerifier(),
        settings=settings,
    )


@pytest.mark.anyio
async def test_heartbeat_aborts_when_parent_session_revoked() -> None:
    repo = _FakeRepo()
    verifier = _FakeSessionVerifier(status="REVOKED")
    owner = _build_state_owner(repo=repo, verifier=verifier)

    principal = RuntimePrincipal(
        subject="usr:1",
        user_id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        roles=("Developer",),
        scopes=frozenset(["runs:write"]),
        session_id=str(uuid4()),
    )

    res = await owner.heartbeat(
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        job_id=str(uuid4()),
        worker_id="worker-1",
        lease_token=str(uuid4()),
        principal=principal,
    )
    assert res is False
    assert "extend_lease" not in repo.calls


@pytest.mark.anyio
async def test_heartbeat_succeeds_when_session_active() -> None:
    repo = _FakeRepo()
    verifier = _FakeSessionVerifier(status="OK")
    owner = _build_state_owner(repo=repo, verifier=verifier)

    res = await owner.heartbeat(
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        job_id=str(uuid4()),
        worker_id="worker-1",
        lease_token=str(uuid4()),
    )
    assert res is True
    assert "extend_lease" in repo.calls


@pytest.mark.anyio
async def test_begin_execution_decrypts_grant_and_authenticates_principal() -> None:
    key = os.urandom(32)
    repo = _FakeRepo()
    outbox = _FakeOutboxPublisher()
    owner = _build_state_owner(repo=repo, outbox=outbox, key=key)

    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    now = datetime.now(UTC)

    grant = ExecutionGrant(
        issuer="agent-runtime-admission",
        subject=f"usr:{uuid4()}",
        user_id=str(uuid4()),
        tenant_id=t_id,
        workspace_id=w_id,
        run_id=r_id,
        parent_session_id=str(uuid4()),
        parent_rotation_version=1,
        original_scopes=("runs:write", "agents:read"),
        granted_at=now.isoformat(),
        authority_expires_at=(now + timedelta(hours=24)).isoformat(),
    )
    ciphertext, _, _ = encrypt_execution_grant(grant, key=key)

    repo.begin_result = BeginExecutionResult(
        success=True,
        started=True,
        authority_ciphertext=ciphertext,
        authority_key_id=DEFAULT_KEY_ID,
    )

    lease_ident = LeaseIdentity(
        tenant_id=t_id,
        workspace_id=w_id,
        job_id=str(uuid4()),
        run_id=r_id,
        leased_by="worker-1",
        lease_token=str(uuid4()),
        attempt=1,
    )

    result, dec_grant, principal = await owner.begin_execution(
        lease_identity=lease_ident,
        require_grant=True,
    )
    assert result.success is True
    assert dec_grant is not None
    assert dec_grant.run_id == r_id
    assert principal is not None
    assert principal.user_id == grant.user_id
    assert "runs:write" in principal.scopes


@pytest.mark.anyio
async def test_begin_execution_fails_closed_on_expired_grant() -> None:
    key = os.urandom(32)
    repo = _FakeRepo()
    outbox = _FakeOutboxPublisher()
    owner = _build_state_owner(repo=repo, outbox=outbox, key=key)

    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    now = datetime.now(UTC)

    # Expired 1 hour ago
    grant = ExecutionGrant(
        issuer="agent-runtime-admission",
        subject=f"usr:{uuid4()}",
        user_id=str(uuid4()),
        tenant_id=t_id,
        workspace_id=w_id,
        run_id=r_id,
        parent_session_id=str(uuid4()),
        parent_rotation_version=1,
        original_scopes=("runs:write",),
        granted_at=(now - timedelta(hours=2)).isoformat(),
        authority_expires_at=(now - timedelta(hours=1)).isoformat(),
    )
    ciphertext, _, _ = encrypt_execution_grant(grant, key=key)

    repo.begin_result = BeginExecutionResult(
        success=True,
        started=True,
        authority_ciphertext=ciphertext,
    )

    lease_ident = LeaseIdentity(
        tenant_id=t_id,
        workspace_id=w_id,
        job_id=str(uuid4()),
        run_id=r_id,
        leased_by="worker-1",
        lease_token=str(uuid4()),
        attempt=1,
    )

    result, _dec_grant, _principal = await owner.begin_execution(
        lease_identity=lease_ident,
        require_grant=True,
    )
    assert result.success is False
    assert result.error_code == "execution_authority_denied"
    assert "fail_execution" in repo.calls


@pytest.mark.anyio
async def test_complete_execution_publishes_event_and_returns_completed_disposition() -> None:
    repo = _FakeRepo()
    outbox = _FakeOutboxPublisher()
    owner = _build_state_owner(repo=repo, outbox=outbox)

    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())

    lease_ident = LeaseIdentity(
        tenant_id=t_id,
        workspace_id=w_id,
        job_id=str(uuid4()),
        run_id=r_id,
        leased_by="worker-1",
        lease_token=str(uuid4()),
        attempt=1,
    )

    disp = await owner.complete_execution(lease_identity=lease_ident)
    assert disp.disposition == ExecutionDispositionType.COMPLETED
    assert disp.terminal is True
    assert "complete_execution" in repo.calls
    assert len(outbox.published) == 1
    assert outbox.published[0]["aggregate_id"] == r_id
