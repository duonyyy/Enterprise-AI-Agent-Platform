"""Unit regression test suite for Agent Promotion API layer (Step 04-D, ADR-022, D01-D09)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from control_api.core.auth import get_current_principal
from control_api.core.database import get_db_session
from control_api.main import app
from control_api.modules.agents.api.dependencies import get_agent_promotion_service
from control_api.modules.agents.models.errors import (
    AgentAccessDeniedError,
    AgentArchivedError,
    AgentNotFoundError,
    AgentStaleRevisionError,
    AgentValidationError,
)
from control_api.modules.agents.models.records import (
    AgentPublicationEntity,
    AgentVersionMetadataEntity,
    PromoteResultEntity,
)
from control_api.modules.agents.services.promotion_service import AgentPromotionService
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal


def _principal(
    role: str = "owner",
    permissions: list[str] | None = None,
) -> AuthenticatedPrincipal:
    perms = permissions
    if perms is None:
        if role in ("owner", "admin"):
            perms = ["agent:*", "workspace:read"]
        elif role == "developer":
            perms = ["agent:read", "agent:create", "agent:edit", "agent:publish", "workspace:read"]
        elif role == "operator":
            perms = ["agent:read", "workspace:read", "approval:read"]
        else:  # viewer
            perms = ["agent:read", "workspace:read"]

    return AuthenticatedPrincipal(
        user_id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=str(uuid4()),
        roles=[role],
        permissions=perms,
    )


def test_d01_list_versions_and_publications_bounded_read() -> None:
    """D01: Verify bounded listing of immutable versions and environment publications."""
    principal = _principal("developer")  # Developers can read versions/publications
    agent_id = uuid4()
    version_id = uuid4()
    now_dt = datetime.now(UTC)

    mock_service = MagicMock(spec=AgentPromotionService)
    mock_service.list_versions = AsyncMock(
        return_value=(
            [
                AgentVersionMetadataEntity(
                    agent_id=agent_id,
                    agent_version_id=version_id,
                    version=2,
                    bundle_sha256="b" * 64,
                    is_immutable=True,
                    created_at=now_dt,
                    published_by=UUID(principal.user_id),
                    model_name="Claude 3.5 Sonnet",
                ),
            ],
            None,
        )
    )
    pub_id = uuid4()
    mock_service.list_publications = AsyncMock(
        return_value=[
            AgentPublicationEntity(
                publication_id=pub_id,
                agent_id=agent_id,
                agent_version_id=version_id,
                version=2,
                environment="production",
                revision=1,
                bundle_sha256="b" * 64,
                published_at=now_dt,
            )
        ]
    )

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            # 1. GET versions
            ver_resp = client.get(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/versions?limit=10"
            )
            assert ver_resp.status_code == 200
            ver_data = ver_resp.json()
            assert len(ver_data["items"]) == 1
            assert ver_data["items"][0]["version"] == 2
            assert ver_data["items"][0]["bundle_sha256"] == "b" * 64
            assert ver_data["items"][0]["model_name"] == "Claude 3.5 Sonnet"
            assert ver_resp.headers.get("Cache-Control") == "no-store"

            # 2. GET publications
            pub_resp = client.get(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/publications"
            )
            assert pub_resp.status_code == 200
            pub_data = pub_resp.json()
            assert len(pub_data["items"]) == 1
            assert pub_data["items"][0]["environment"] == "production"
            assert pub_data["items"][0]["revision"] == 1
            assert pub_resp.headers.get("Cache-Control") == "no-store"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_d02_role_matrix_authorization() -> None:
    """D02: R04-ENV: Owner and Admin can promote; Developer, Operator, Viewer denied 403."""
    agent_id = uuid4()
    version_id = uuid4()
    now_dt = datetime.now(UTC)

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_db_session] = database_override

        for role, should_allow in [
            ("owner", True),
            ("admin", True),
            ("developer", False),
            ("operator", False),
            ("viewer", False),
        ]:
            principal = _principal(role)
            mock_service = MagicMock(spec=AgentPromotionService)
            if should_allow:
                mock_service.promote_version = AsyncMock(
                    return_value=PromoteResultEntity(
                        publication_id=uuid4(),
                        agent_id=agent_id,
                        agent_version_id=version_id,
                        version=1,
                        environment="production",
                        revision=1,
                        bundle_sha256="c" * 64,
                        published_at=now_dt,
                        is_noop=False,
                    )
                )
            else:
                # Service raises access denied if called
                mock_service.promote_version = AsyncMock(
                    side_effect=AgentAccessDeniedError("Only workspace Owner or Admin can promote")
                )

            app.dependency_overrides[get_current_principal] = lambda p=principal: p
            app.dependency_overrides[get_agent_promotion_service] = lambda m=mock_service: m

            with TestClient(app) as client:
                resp = client.post(
                    f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                    json={
                        "agent_version_id": str(version_id),
                        "environment": "production",
                        "expected_publication_revision": 0,
                    },
                )
                if should_allow:
                    assert resp.status_code == 200, f"Role {role} should be allowed"
                    assert resp.json()["revision"] == 1
                else:
                    assert resp.status_code == 403, f"Role {role} must be denied 403"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_d03_first_promotion_and_stale_revision_412() -> None:
    """D03: Expected revision 0 creates pointer; stale revision returns 412."""
    principal = _principal("owner")
    agent_id = uuid4()
    version_id = uuid4()

    mock_service = MagicMock(spec=AgentPromotionService)
    # When expected_publication_revision != pointer revision -> raises AgentStaleRevisionError
    mock_service.promote_version = AsyncMock(
        side_effect=AgentStaleRevisionError("Stale publication revision: expected 1 but pointer does not exist")
    )

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "staging",
                    "expected_publication_revision": 1,  # Stale, should have been 0
                },
            )
            assert resp.status_code == 412
            assert resp.json()["detail"]["code"] == "stale_revision"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_d04_promote_noop_same_target_version() -> None:
    """D04: Same target version with expected revision returns 200 with is_noop=True."""
    principal = _principal("admin")
    agent_id = uuid4()
    version_id = uuid4()
    pub_id = uuid4()
    now_dt = datetime.now(UTC)

    mock_service = MagicMock(spec=AgentPromotionService)
    mock_service.promote_version = AsyncMock(
        return_value=PromoteResultEntity(
            publication_id=pub_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            version=1,
            environment="development",
            revision=1,
            bundle_sha256="d" * 64,
            published_at=now_dt,
            is_noop=True,
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
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "development",
                    "expected_publication_revision": 1,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_noop"] is True
            assert data["revision"] == 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_d05_target_not_found_or_archived_or_unsealed() -> None:
    """D05: Target version not found -> 404, archived agent -> 409, unsealed -> 422."""
    principal = _principal("owner")
    agent_id = uuid4()
    version_id = uuid4()

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override

        # 1. 404 target version not found
        mock_service = MagicMock(spec=AgentPromotionService)
        mock_service.promote_version = AsyncMock(
            side_effect=AgentNotFoundError("Target version not found for this agent")
        )
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "production",
                    "expected_publication_revision": 0,
                },
            )
            assert resp.status_code == 404

        # 2. 409 archived agent
        mock_service.promote_version = AsyncMock(
            side_effect=AgentArchivedError("Archived agent cannot be promoted")
        )
        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "production",
                    "expected_publication_revision": 0,
                },
            )
            assert resp.status_code == 409

        # 3. 422 unsealed agent version
        mock_service.promote_version = AsyncMock(
            side_effect=AgentValidationError("Cannot promote an unsealed agent version", code="unsealed_version")
        )
        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "production",
                    "expected_publication_revision": 0,
                },
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_d07_rollback_promotes_old_version() -> None:
    """D07: Rollback is a promotion selecting an older version; returns 200 with new revision."""
    principal = _principal("owner")
    agent_id = uuid4()
    v1_id = uuid4()
    pub_id = uuid4()
    now_dt = datetime.now(UTC)

    mock_service = MagicMock(spec=AgentPromotionService)
    # Rollback from v2 to v1 increases pointer revision from 2 to 3
    mock_service.promote_version = AsyncMock(
        return_value=PromoteResultEntity(
            publication_id=pub_id,
            agent_id=agent_id,
            agent_version_id=v1_id,
            version=1,
            environment="production",
            revision=3,
            bundle_sha256="a" * 64,
            published_at=now_dt,
            is_noop=False,
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
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(v1_id),
                    "environment": "production",
                    "expected_publication_revision": 2,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["version"] == 1
            assert data["revision"] == 3
            assert data["is_noop"] is False
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@pytest.mark.parametrize(
    "bad_revision",
    [False, True, "0", "1", 0.5, 1.5, -1, 2_147_483_648, 2**53],
)
def test_rf04_04_promote_rejects_coerced_and_oversized_publication_revisions(
    bad_revision: Any,
) -> None:
    """RF04-04: expected_publication_revision rejects bools, strings, fractions, negatives, and > int32 max."""
    principal = _principal(role="owner")
    agent_id = uuid4()
    version_id = uuid4()

    mock_service = MagicMock(spec=AgentPromotionService)
    mock_service.promote_version = AsyncMock()

    session = MagicMock()
    session.commit = AsyncMock()

    async def database_override():
        yield session

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_db_session] = database_override
        app.dependency_overrides[get_agent_promotion_service] = lambda: mock_service

        with TestClient(app) as client:
            resp = client.post(
                f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/promotions",
                json={
                    "agent_version_id": str(version_id),
                    "environment": "production",
                    "expected_publication_revision": bad_revision,
                },
            )
            assert resp.status_code == 422, resp.text
            mock_service.promote_version.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_rf04_04_promote_accepts_zero_whole_float_and_int32_max() -> None:
    """RF04-04: Initial revision 0, whole-number 0.0/1.0, and int32 max are accepted."""
    from control_api.modules.agents.dto.requests import PromoteAgentVersionRequestDTO

    for raw, expected in [(0, 0), (0.0, 0), (1.0, 1), (2_147_483_647, 2_147_483_647)]:
        dto = PromoteAgentVersionRequestDTO.model_validate(
            {
                "agent_version_id": str(uuid4()),
                "environment": "development",
                "expected_publication_revision": raw,
            }
        )
        assert dto.expected_publication_revision == expected
        assert type(dto.expected_publication_revision) is int
