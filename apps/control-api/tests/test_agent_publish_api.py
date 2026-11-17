"""Unit regression suite for Agent Publish API layer (ADR-022, SPEC-AGENT-001, STEP-04C)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from control_api.core.auth import get_current_principal
from control_api.core.database import get_db_session
from control_api.main import app
from control_api.modules.agents.api.dependencies import get_agent_publish_service
from control_api.modules.agents.models.errors import (
    AgentIdempotencyConflictError,
    AgentNotFoundError,
    AgentStaleRevisionError,
)
from control_api.modules.agents.models.ports import AgentRepositoryPort
from control_api.modules.agents.models.records import PublishResultEntity
from control_api.modules.agents.services.publish_service import AgentPublishService
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal


def _principal(permissions: list[str] | None = None) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        roles=["developer"],
        permissions=permissions or ["agent:publish", "agent:read"],
    )


def test_publish_200_valid_revision() -> None:
    principal = _principal()
    agent_id = uuid4()
    version_id = uuid4()
    now_dt = datetime.now(UTC)

    mock_service = MagicMock(spec=AgentPublishService)
    mock_service.publish_agent_revision = AsyncMock(
        return_value=PublishResultEntity(
            agent_id=agent_id,
            agent_version_id=version_id,
            version=1,
            bundle_sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            is_immutable=True,
            created_at=now_dt,
            is_replay=False,
        )
    )

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={
                    "Idempotency-Key": "publish-op-key-001",
                    "Content-Type": "application/json",
                },
                json={"expected_revision": 1},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == str(agent_id)
        assert data["agent_version_id"] == str(version_id)
        assert data["version"] == 1
        assert (
            data["bundle_sha256"]
            == "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        assert data["is_immutable"] is True
        assert data["created_at"] == now_dt.isoformat()
        assert data["spec_hash"] == data["bundle_sha256"]
        assert response.headers.get("Cache-Control") == "no-store"
        session.commit.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@pytest.mark.parametrize(
    "idemp_header,expected_code",
    [
        (None, "missing_idempotency_key"),
        ("", "missing_idempotency_key"),
        ("   ", "missing_idempotency_key"),
        ("x" * 257, "invalid_idempotency_key"),
        ("key\nwith-newline", "invalid_idempotency_key"),
        ("key\rwith-cr", "invalid_idempotency_key"),
    ],
)
def test_publish_400_missing_or_invalid_idempotency_key(
    idemp_header: str | None, expected_code: str
) -> None:
    principal = _principal()
    agent_id = uuid4()
    mock_service = MagicMock(spec=AgentPublishService)

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        headers = {"Content-Type": "application/json"}
        if idemp_header is not None:
            headers["Idempotency-Key"] = idemp_header

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers=headers,
                json={"expected_revision": 1},
            )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == expected_code
        mock_service.publish_agent_revision.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"expected_revision": 0},
        {"expected_revision": -1},
        {"expected_revision": "invalid"},
        {"expected_revision": 1, "extra_field": "disallowed"},
    ],
)
def test_publish_422_invalid_request_body(body: dict[str, Any]) -> None:
    principal = _principal()
    agent_id = uuid4()
    mock_service = MagicMock(spec=AgentPublishService)

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={"Idempotency-Key": "test-key-01"},
                json=body,
            )

        assert response.status_code == 422
        mock_service.publish_agent_revision.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_publish_412_stale_revision() -> None:
    principal = _principal()
    agent_id = uuid4()
    mock_service = MagicMock(spec=AgentPublishService)
    mock_service.publish_agent_revision = AsyncMock(
        side_effect=AgentStaleRevisionError("Stale revision: expected 1, committed is 2")
    )

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={"Idempotency-Key": "test-key-01"},
                json={"expected_revision": 1},
            )

        assert response.status_code == 412
        assert response.json()["detail"]["code"] == "stale_revision"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_publish_409_idempotency_conflict() -> None:
    principal = _principal()
    agent_id = uuid4()
    mock_service = MagicMock(spec=AgentPublishService)
    mock_service.publish_agent_revision = AsyncMock(
        side_effect=AgentIdempotencyConflictError(
            "Idempotency key collision with different request payload"
        )
    )

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={"Idempotency-Key": "test-key-01"},
                json={"expected_revision": 1},
            )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_publish_403_insufficient_permissions() -> None:
    principal = _principal(permissions=["agent:read"])  # lacks agent:publish
    agent_id = uuid4()

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={"Idempotency-Key": "test-key-01"},
                json={"expected_revision": 1},
            )

        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_publish_404_not_found() -> None:
    principal = _principal()
    agent_id = uuid4()
    mock_service = MagicMock(spec=AgentPublishService)
    mock_service.publish_agent_revision = AsyncMock(
        side_effect=AgentNotFoundError("Agent not found in workspace")
    )

    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_publish_service] = lambda: mock_service

        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publish",
                headers={"Idempotency-Key": "test-key-01"},
                json={"expected_revision": 1},
            )

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "agent_not_found"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_publish_service_computes_request_digest_and_delegates() -> None:
    mock_repo = MagicMock(spec=AgentRepositoryPort)
    mock_repo.publish_version = AsyncMock(
        return_value=PublishResultEntity(
            agent_id=uuid4(),
            agent_version_id=uuid4(),
            version=1,
            bundle_sha256="abc",
            is_immutable=True,
            created_at=datetime.now(UTC),
            is_replay=False,
        )
    )

    service = AgentPublishService(mock_repo)
    principal = _principal()
    agent_id = uuid4()
    session = MagicMock()

    result = asyncio.run(
        service.publish_agent_revision(
            session,
            principal=principal,
            agent_id=agent_id,
            expected_revision=1,
            operation_key="op-key-01",
            body={"expected_revision": 1},
        )
    )

    assert result is mock_repo.publish_version.return_value
    mock_repo.publish_version.assert_awaited_once()
    call_kwargs = mock_repo.publish_version.call_args.kwargs
    assert call_kwargs["workspace_id"] == UUID(principal.workspace_id)
    assert call_kwargs["agent_id"] == agent_id
    assert call_kwargs["expected_revision"] == 1
    assert call_kwargs["operation_key"] == "op-key-01"
    assert len(call_kwargs["request_sha256"]) == 64
