"""Agent service boundary regressions; these doubles do not prove PostgreSQL behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from control_api.core.auth import get_current_principal
from control_api.core.database import get_db_session
from control_api.main import app
from control_api.modules.agents.api._http_helpers import translate_agent_error
from control_api.modules.agents.api.dependencies import get_agent_service
from control_api.modules.agents.models.errors import AgentValidationError
from control_api.modules.agents.models.ports import AgentRepositoryPort
from control_api.modules.agents.models.records import AgentDraftEntity
from control_api.modules.agents.services.agent_service import AgentDraftService
from control_api.modules.identity.services.session_service import session_manager
from fastapi import HTTPException
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy.exc import SQLAlchemyError, StatementError
from starlette.requests import Request


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        permissions=["agent:read"],
    )


def _spec() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "test-agent"},
        "mode": "fixed_plan",
        "modelRef": "unresolved-test-model",
        "persona": {"instructions": "Test", "extension": None},
        "limits": {"maxSteps": 8},
    }


def test_service_uses_injected_port_and_preserves_raw_spec() -> None:
    repository = MagicMock(spec=AgentRepositoryPort)
    repository.create_draft = AsyncMock(return_value=object())
    session = MagicMock()
    principal, spec = _principal(), _spec()
    result = asyncio.run(
        AgentDraftService(repository).create_draft(
            session,
            principal=principal,
            slug="test-agent",
            display_name="Test",
            spec=spec,
        )
    )
    assert result is repository.create_draft.return_value
    call = repository.create_draft.call_args
    assert call.args == (session,)
    assert call.kwargs["spec"] is spec
    assert call.kwargs["spec"]["persona"]["extension"] is None
    assert call.kwargs["workspace_id"] == UUID(principal.workspace_id)


@pytest.mark.parametrize("invalid_schema", [True, False])
def test_invalid_spec_or_slug_does_not_reach_repository(invalid_schema: bool) -> None:
    repository = MagicMock(spec=AgentRepositoryPort)
    repository.create_draft = AsyncMock()
    spec = _spec()
    if invalid_schema:
        spec.pop("schemaVersion")
    else:
        spec["metadata"]["name"] = "another-slug"
    with pytest.raises(AgentValidationError):
        asyncio.run(
            AgentDraftService(repository).create_draft(
                MagicMock(),
                principal=_principal(),
                slug="test-agent",
                display_name="Test",
                spec=spec,
            )
        )
    repository.create_draft.assert_not_awaited()


def test_router_uses_new_dependency_provider() -> None:
    principal = _principal()
    service = MagicMock(spec=AgentDraftService)
    service.list_drafts = AsyncMock(return_value=([], None))
    session = MagicMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_service] = lambda: service
        with TestClient(app) as client:
            response = client.get(f"/v1/workspaces/{principal.workspace_id}/agents")
        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None}
        service.list_drafts.assert_awaited_once_with(
            session,
            principal=principal,
            limit=25,
            cursor=None,
        )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@pytest.mark.parametrize("commit_fails", [False, True])
def test_create_confirms_commit_before_reporting_success(commit_fails: bool) -> None:
    principal = _principal()
    principal.permissions.append("agent:create")
    service = MagicMock(spec=AgentDraftService)
    service.create_draft = AsyncMock(
        return_value=AgentDraftEntity(
            agent_id=uuid4(),
            slug="test-agent",
            display_name="Test",
            revision=1,
            archived_at=None,
            created_at=datetime.now(UTC),
            spec=_spec(),
        )
    )
    session = MagicMock()
    session.commit = AsyncMock(
        side_effect=SQLAlchemyError("private DB diagnostic") if commit_fails else None
    )

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_service] = lambda: service
        with TestClient(app) as client:
            response = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents",
                json={
                    "slug": "test-agent",
                    "display_name": "Test",
                    "spec": _spec(),
                },
            )
        session.commit.assert_awaited_once()
        assert response.status_code == (503 if commit_fails else 201)
        assert "private DB diagnostic" not in response.text
        if not commit_fails:
            assert response.headers["etag"] == '"1"'
            assert response.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_database_error_translation_does_not_log_statement_parameters(caplog) -> None:
    secret = "fixture-sensitive-instruction"
    try:
        raise StatementError("db failed", "INSERT", {"spec": secret}, Exception(secret))
    except StatementError as exc:
        response = translate_agent_error(exc)
    assert response.status_code == 503
    assert secret not in str(response.detail)
    assert secret not in caplog.text


@pytest.mark.parametrize("authorization", ["Basic invalid", "Bearer", "", "invalid"])
def test_explicit_malformed_auth_header_cannot_fall_back_to_session_cookie(
    authorization: str,
) -> None:
    request = Request({"type": "http", "headers": [(b"cookie", b"session=fixture-cookie")]})
    with (
        patch.object(session_manager, "verify_session_token") as verify_cookie,
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(get_current_principal(str(uuid4()), request, authorization, MagicMock()))
    assert error.value.status_code == 401
    verify_cookie.assert_not_called()
