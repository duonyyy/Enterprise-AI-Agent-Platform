"""Unit and HTTP boundary tests for 06-D run cancellation, queue expiry, and maintenance CLI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from agent_runtime.api import runs
from agent_runtime.application.run_lifecycle import (
    CancelRunResult,
    RunLifecycleAccessDeniedError,
    RunLifecycleConflictError,
    RunLifecycleNotFoundError,
    RunLifecycleValidationError,
)
from agent_runtime.application.run_lifecycle_service import RunLifecycleService
from agent_runtime.auth import RuntimePrincipal, get_runtime_principal
from agent_runtime.main import app
from agent_runtime.maintenance import main as maintenance_main
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_lifecycle_repository import RunLifecycleRepository
from fastapi.testclient import TestClient


class _FakeAdmissionRepo(RunAdmissionRepository):
    def __init__(self, role: str = "developer") -> None:
        self.role = role

    async def verify_active_workspace_membership(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        for_share: bool = False,
    ) -> str:
        _ = (session, tenant_id, workspace_id, user_id, for_share)
        return self.role


class _FakeLifecycleRepo(RunLifecycleRepository):
    def __init__(self, canned_result: CancelRunResult | Exception) -> None:
        self.canned_result = canned_result
        self.calls: list[dict[str, Any]] = []

    async def cancel_or_expire_run(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        actor_subject: str,
        caller_role: str | None = None,
        caller_user_id: str | None = None,
        is_system_maintenance: bool = False,
        now_override: Any = None,
    ) -> CancelRunResult:
        _ = (session, now_override)
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "actor_subject": actor_subject,
                "caller_role": caller_role,
                "caller_user_id": caller_user_id,
                "is_system_maintenance": is_system_maintenance,
            }
        )
        if isinstance(self.canned_result, Exception):
            raise self.canned_result
        return self.canned_result


@asynccontextmanager
async def _dummy_session(t: str, w: str, u: str | None = None) -> Any:
    _ = (t, w, u)
    yield object()


def _principal(
    role: str = "developer",
    user_id: str | None = None,
    scopes: list[str] | None = None,
) -> RuntimePrincipal:
    uid = user_id or str(uuid4())
    sc = frozenset(scopes or ["run:read", "run:start"])
    return RuntimePrincipal(
        subject="user-subject",
        user_id=uid,
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        roles=(role,),
        scopes=sc,
        token_scopes=sc,
        session_id=str(uuid4()),
    )


# ---------------------------------------------------------------------------
# Unit tests: Service & Authority Layer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_requires_run_start_scope() -> None:
    service = RunLifecycleService(session_factory=_dummy_session)
    p = _principal(role="viewer", scopes=["run:read"])
    with pytest.raises(RunLifecycleAccessDeniedError) as exc:
        await service.cancel_run(principal=p, run_id=str(uuid4()))
    assert "run:start" in str(exc.value)


@pytest.mark.anyio
async def test_cancel_rejects_invalid_uuid() -> None:
    service = RunLifecycleService(session_factory=_dummy_session)
    p = _principal()
    with pytest.raises(RunLifecycleValidationError) as exc:
        await service.cancel_run(principal=p, run_id="not-a-valid-uuid")
    assert exc.value.code == "invalid_uuid"


@pytest.mark.anyio
async def test_cancel_delegates_to_repo_when_authorized() -> None:
    run_id = str(uuid4())
    expected = CancelRunResult(
        run_id=run_id,
        status="cancelled",
        changed=True,
        disposition="cancelled",
    )
    fake_repo = _FakeLifecycleRepo(expected)
    fake_adm = _FakeAdmissionRepo(role="operator")
    service = RunLifecycleService(
        session_factory=_dummy_session,
        lifecycle_repository=fake_repo,
        admission_repository=fake_adm,
    )
    p = _principal(role="operator")
    res = await service.cancel_run(principal=p, run_id=run_id)

    assert res == expected
    assert len(fake_repo.calls) == 1
    call = fake_repo.calls[0]
    assert call["run_id"] == run_id
    assert call["caller_role"] == "operator"
    assert call["caller_user_id"] == p.user_id


# ---------------------------------------------------------------------------
# HTTP Route tests via FastAPI TestClient
# ---------------------------------------------------------------------------


def test_http_cancel_queued_run_success() -> None:
    run_id = str(uuid4())
    p = _principal(role="admin")

    expected = CancelRunResult(
        run_id=run_id,
        status="cancelled",
        changed=True,
        disposition="cancelled",
    )
    fake_repo = _FakeLifecycleRepo(expected)
    fake_adm = _FakeAdmissionRepo(role="admin")
    svc = RunLifecycleService(
        session_factory=_dummy_session,
        lifecycle_repository=fake_repo,
        admission_repository=fake_adm,
    )

    app.dependency_overrides[get_runtime_principal] = lambda: p
    app.dependency_overrides[runs.get_run_lifecycle_service] = lambda: svc
    try:
        with TestClient(app) as client:
            resp = client.post(f"/v1/runs/{run_id}/cancel", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == run_id
            assert data["status"] == "cancelled"
            assert data["changed"] is True
            assert data["disposition"] == "cancelled"
    finally:
        app.dependency_overrides.clear()


def test_http_cancel_idempotent_terminal_returns_200_unchanged() -> None:
    run_id = str(uuid4())
    p = _principal(role="developer")

    expected = CancelRunResult(
        run_id=run_id,
        status="cancelled",
        changed=False,
        disposition="already_terminal",
    )
    svc = RunLifecycleService(
        session_factory=_dummy_session,
        lifecycle_repository=_FakeLifecycleRepo(expected),
        admission_repository=_FakeAdmissionRepo(role="developer"),
    )

    app.dependency_overrides[get_runtime_principal] = lambda: p
    app.dependency_overrides[runs.get_run_lifecycle_service] = lambda: svc
    try:
        with TestClient(app) as client:
            resp = client.post(f"/v1/runs/{run_id}/cancel", json={})
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == run_id
            assert data["status"] == "cancelled"
            assert data["changed"] is False
            assert data["disposition"] == "already_terminal"
    finally:
        app.dependency_overrides.clear()


def test_http_cancel_rejects_non_empty_body_with_422() -> None:
    run_id = str(uuid4())
    p = _principal()

    app.dependency_overrides[get_runtime_principal] = lambda: p
    try:
        with TestClient(app) as client:
            resp = client.post(
                f"/v1/runs/{run_id}/cancel",
                json={"reason": "unauthorized_extra_field"},
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_http_cancel_in_flight_returns_409() -> None:
    run_id = str(uuid4())
    p = _principal(role="operator")
    err = RunLifecycleConflictError("Run is currently executing", code="run_in_flight")
    svc = RunLifecycleService(
        session_factory=_dummy_session,
        lifecycle_repository=_FakeLifecycleRepo(err),
        admission_repository=_FakeAdmissionRepo(role="operator"),
    )

    app.dependency_overrides[get_runtime_principal] = lambda: p
    app.dependency_overrides[runs.get_run_lifecycle_service] = lambda: svc
    try:
        with TestClient(app) as client:
            resp = client.post(f"/v1/runs/{run_id}/cancel", json={})
            assert resp.status_code == 409
            assert resp.json()["detail"]["code"] == "run_in_flight"
    finally:
        app.dependency_overrides.clear()


def test_http_cancel_peer_run_as_developer_returns_404() -> None:
    run_id = str(uuid4())
    p = _principal(role="developer")
    err = RunLifecycleNotFoundError("Run not found in workspace", code="run_not_found")
    svc = RunLifecycleService(
        session_factory=_dummy_session,
        lifecycle_repository=_FakeLifecycleRepo(err),
        admission_repository=_FakeAdmissionRepo(role="developer"),
    )

    app.dependency_overrides[get_runtime_principal] = lambda: p
    app.dependency_overrides[runs.get_run_lifecycle_service] = lambda: svc
    try:
        with TestClient(app) as client:
            resp = client.post(f"/v1/runs/{run_id}/cancel", json={})
            assert resp.status_code == 404
            assert resp.json()["detail"]["code"] == "run_not_found"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Maintenance CLI unit tests
# ---------------------------------------------------------------------------


def test_maintenance_cli_validates_uuids() -> None:
    assert (
        maintenance_main(["--task", "expire-queued", "--tenant-id", "bad", "--workspace-id", "bad"])
        == 2
    )


def test_maintenance_cli_validates_limits() -> None:
    t = str(uuid4())
    w = str(uuid4())
    assert (
        maintenance_main(
            ["--task", "expire-queued", "--tenant-id", t, "--workspace-id", w, "--limit", "0"]
        )
        == 2
    )
    assert (
        maintenance_main(
            ["--task", "expire-queued", "--tenant-id", t, "--workspace-id", w, "--limit", "101"]
        )
        == 2
    )


def test_maintenance_cli_retention_rejects_apply() -> None:
    t = str(uuid4())
    w = str(uuid4())
    assert (
        maintenance_main(
            ["--task", "retention-report", "--tenant-id", t, "--workspace-id", w, "--apply"]
        )
        == 2
    )


def test_maintenance_cli_expire_queued_apply_fails_closed_without_config() -> None:
    t = str(uuid4())
    w = str(uuid4())
    # By default, run_maintenance_mutations_enabled is False
    assert (
        maintenance_main(
            ["--task", "expire-queued", "--tenant-id", t, "--workspace-id", w, "--apply"]
        )
        == 2
    )
