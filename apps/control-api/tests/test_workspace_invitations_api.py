"""Unit, RBAC, CSRF, and security-boundary tests for Workspace Invitations HTTP API."""

from __future__ import annotations

import ast
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from control_api.core.auth import (
    ROLE_PERMISSIONS,
    AuthenticatedUserContext,
    SessionWorkspaceContext,
    get_current_authenticated_user,
    get_current_session_workspace_context,
)
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.models.session import SessionData
from control_api.modules.workspaces.models.errors import (
    InvitationConflictError,
    InvitationNotFoundError,
    InvitationRateLimitError,
)
from control_api.modules.workspaces.models.records import InvitationRecord
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal

workspaces_router = importlib.import_module("control_api.modules.workspaces.api.router")


def _sample_record(
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    actor_user_id: UUID,
    invitation_id: UUID | None = None,
    role: str = "developer",
    status: str = "pending",
    generation: int = 1,
    delivery_state: str = "queued",
) -> InvitationRecord:
    now = datetime.now(UTC)
    return InvitationRecord(
        invitation_id=str(invitation_id or uuid4()),
        tenant_id=str(tenant_id),
        workspace_id=str(workspace_id),
        recipient_email_normalized="invitee+dev@example.com",
        recipient_issuer="http://localhost:8080/realms/enterprise",
        role=role,
        status=status,
        generation=generation,
        delivery_state=delivery_state,
        delivery_error_code=None,
        created_by_user_id=str(actor_user_id),
        delivery_requested_by_user_id=str(actor_user_id),
        revoked_by_user_id=str(actor_user_id) if status == "revoked" else None,
        accepted_by_user_id=None,
        expires_at=(now + timedelta(hours=72)).isoformat(),
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        provider_accepted_at=None,
        revoked_at=now.isoformat() if status == "revoked" else None,
        accepted_at=None,
        audit_event_id=str(uuid4()),
        outbox_event_id=str(uuid4()) if status == "pending" else None,
    )


@pytest.fixture()
def isolated_client(monkeypatch: pytest.MonkeyPatch):
    fernet_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "invitations_enabled", True)
    monkeypatch.setattr(settings, "invitation_public_app_origin", "http://localhost:3000")
    monkeypatch.setattr(settings, "invitation_proof_key_id", "v1")
    monkeypatch.setattr(
        settings.invitation_proof_encryption_key, "get_secret_value", lambda: fernet_key
    )

    yield TestClient(app)
    app.dependency_overrides.clear()


def test_clean_architecture_no_fastapi_in_invitation_domain_layers():
    root = Path(__file__).resolve().parents[1] / "src" / "control_api" / "modules" / "workspaces"
    for layer in ("models", "dto", "repositories", "services"):
        for py_file in (root / layer).glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("fastapi"), f"fastapi import in {py_file}"
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert not mod.startswith("fastapi"), f"fastapi import in {py_file}"


def test_invitations_require_active_session_not_scoped_token(isolated_client: TestClient):
    ws_id = uuid4()
    user_id = uuid4()
    app.dependency_overrides[get_current_authenticated_user] = lambda: AuthenticatedUserContext(
        user_id=str(user_id),
        session_data=None,
        issuer=settings.jwt_issuer,
        subject=str(user_id),
    )
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    resp = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "invitee@example.com", "role": "developer"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "active_session_required"


def test_cookie_csrf_blocks_untrusted_cross_site_origin_on_invitations(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = uuid4()
    ws_id = uuid4()
    user_id = uuid4()
    ctx = SessionWorkspaceContext(
        principal=AuthenticatedPrincipal(
            user_id=str(user_id),
            workspace_id=str(ws_id),
            tenant_id=str(tenant_id),
            roles=["owner"],
            permissions=ROLE_PERMISSIONS["owner"],
        ),
        session_user=AuthenticatedUserContext(
            user_id=str(user_id),
            session_data=SessionData(
                session_id=str(uuid4()),
                user_id=str(user_id),
                issuer="http://localhost:8080/realms/enterprise",
                subject="alice",
                created_at=1000,
                idle_expires_at=9999999999,
                absolute_expires_at=9999999999,
                rotation_version=1,
            ),
            issuer="http://localhost:8080/realms/enterprise",
            subject="alice",
        ),
    )
    app.dependency_overrides[get_current_session_workspace_context] = lambda: ctx
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    isolated_client.cookies.set(settings.session_cookie_name, "dummy-cookie")
    resp = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        headers={"Origin": "https://evil.example.net"},
        json={"email": "invitee@example.com", "role": "developer"},
    )
    assert resp.status_code == 403
    assert "Origin" in resp.json()["detail"]


def test_role_matrix_validation_and_redaction_on_create_list_resend_revoke(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = uuid4()
    ws_id = uuid4()
    actor_id = uuid4()
    inv_id = uuid4()

    def _set_role(role: str) -> None:
        ctx = SessionWorkspaceContext(
            principal=AuthenticatedPrincipal(
                user_id=str(actor_id),
                workspace_id=str(ws_id),
                tenant_id=str(tenant_id),
                roles=[role],
                permissions=ROLE_PERMISSIONS[role],
            ),
            session_user=AuthenticatedUserContext(
                user_id=str(actor_id),
                session_data=SessionData(
                    session_id=str(uuid4()),
                    user_id=str(actor_id),
                    issuer="http://localhost:8080/realms/enterprise",
                    subject="actor",
                    created_at=1000,
                    idle_expires_at=9999999999,
                    absolute_expires_at=9999999999,
                    rotation_version=1,
                ),
                issuer="http://localhost:8080/realms/enterprise",
                subject="actor",
            ),
        )
        app.dependency_overrides[get_current_session_workspace_context] = lambda: ctx
        app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    fake_repo = SimpleNamespace(
        create_invitation=AsyncMock(
            return_value=_sample_record(
                tenant_id=tenant_id,
                workspace_id=ws_id,
                actor_user_id=actor_id,
                invitation_id=inv_id,
            )
        ),
        list_invitations=AsyncMock(
            return_value=(
                [
                    _sample_record(
                        tenant_id=tenant_id,
                        workspace_id=ws_id,
                        actor_user_id=actor_id,
                        invitation_id=inv_id,
                    )
                ],
                "next-opaque-cursor",
            )
        ),
        resend_invitation=AsyncMock(
            return_value=_sample_record(
                tenant_id=tenant_id,
                workspace_id=ws_id,
                actor_user_id=actor_id,
                invitation_id=inv_id,
                generation=2,
            )
        ),
        revoke_invitation=AsyncMock(
            return_value=_sample_record(
                tenant_id=tenant_id,
                workspace_id=ws_id,
                actor_user_id=actor_id,
                invitation_id=inv_id,
                status="revoked",
                delivery_state="cancelled",
            )
        ),
    )
    fake_repo.bind = lambda _session: fake_repo
    monkeypatch.setattr(workspaces_router, "invitation_repository", fake_repo)

    # Developer cannot create or list invitations
    _set_role("developer")
    dev_resp = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "x@example.com", "role": "viewer"},
    )
    assert dev_resp.status_code == 403

    # Admin cannot invite owner (422) or admin (403)
    _set_role("admin")
    owner_attempt = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "x@example.com", "role": "owner"},
    )
    assert owner_attempt.status_code == 422

    admin_attempt = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "x@example.com", "role": "admin"},
    )
    assert admin_attempt.status_code == 403
    assert admin_attempt.json()["detail"]["code"] == "admin_cannot_invite_admin"

    # Owner succeeds on create (201), list (200), resend (202), revoke (200), zero secrets leaked
    _set_role("owner")
    created = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "Invitee+Dev@Example.COM", "role": "developer"},
    )
    assert created.status_code == 201
    created_body = created.json()
    for forbidden in (
        "proof",
        "token_hash",
        "proof_ciphertext",
        "proof_key_id",
        "invite_url",
        "lease_token",
    ):
        assert forbidden not in created_body

    listed = isolated_client.get(f"/v1/workspaces/{ws_id}/invitations?limit=10")
    assert listed.status_code == 200
    assert listed.json()["next_cursor"] == "next-opaque-cursor"

    resent = isolated_client.post(f"/v1/workspaces/{ws_id}/invitations/{inv_id}/resend")
    assert resent.status_code == 202
    assert resent.json()["generation"] == 2

    revoked = isolated_client.post(f"/v1/workspaces/{ws_id}/invitations/{inv_id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


def test_invitations_fail_closed_when_disabled_or_misconfigured(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = uuid4()
    ws_id = uuid4()
    actor_id = uuid4()
    ctx = SessionWorkspaceContext(
        principal=AuthenticatedPrincipal(
            user_id=str(actor_id),
            workspace_id=str(ws_id),
            tenant_id=str(tenant_id),
            roles=["owner"],
            permissions=ROLE_PERMISSIONS["owner"],
        ),
        session_user=AuthenticatedUserContext(
            user_id=str(actor_id),
            session_data=SessionData(
                session_id=str(uuid4()),
                user_id=str(actor_id),
                issuer="http://localhost:8080/realms/enterprise",
                subject="owner",
                created_at=1000,
                idle_expires_at=9999999999,
                absolute_expires_at=9999999999,
                rotation_version=1,
            ),
            issuer="http://localhost:8080/realms/enterprise",
            subject="owner",
        ),
    )
    app.dependency_overrides[get_current_session_workspace_context] = lambda: ctx
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    monkeypatch.setattr(settings, "invitations_enabled", False)
    r_disabled = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "invitee@example.com", "role": "viewer"},
    )
    assert r_disabled.status_code == 503
    assert r_disabled.json()["detail"]["code"] == "invitation_service_unavailable"

    monkeypatch.setattr(settings, "invitations_enabled", True)
    monkeypatch.setattr(
        settings.invitation_proof_encryption_key, "get_secret_value", lambda: "bad-key"
    )
    r_bad_key = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "invitee@example.com", "role": "viewer"},
    )
    assert r_bad_key.status_code == 503
    assert r_bad_key.json()["detail"]["code"] == "invitation_configuration_invalid"


def test_error_translation_preserves_404_409_429_retry_after(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = uuid4()
    ws_id = uuid4()
    actor_id = uuid4()
    inv_id = uuid4()
    ctx = SessionWorkspaceContext(
        principal=AuthenticatedPrincipal(
            user_id=str(actor_id),
            workspace_id=str(ws_id),
            tenant_id=str(tenant_id),
            roles=["owner"],
            permissions=ROLE_PERMISSIONS["owner"],
        ),
        session_user=AuthenticatedUserContext(
            user_id=str(actor_id),
            session_data=SessionData(
                session_id=str(uuid4()),
                user_id=str(actor_id),
                issuer="http://localhost:8080/realms/enterprise",
                subject="owner",
                created_at=1000,
                idle_expires_at=9999999999,
                absolute_expires_at=9999999999,
                rotation_version=1,
            ),
            issuer="http://localhost:8080/realms/enterprise",
            subject="owner",
        ),
    )
    app.dependency_overrides[get_current_session_workspace_context] = lambda: ctx
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    fake_repo = SimpleNamespace(
        create_invitation=AsyncMock(
            side_effect=InvitationConflictError("pending_invitation_conflict", "Already invited")
        ),
        resend_invitation=AsyncMock(
            side_effect=InvitationRateLimitError(
                "resend_cooldown_active",
                "Cooldown active",
                retry_after_seconds=42,
            )
        ),
        revoke_invitation=AsyncMock(
            side_effect=InvitationNotFoundError("invitation_not_found", "Not found")
        ),
    )
    fake_repo.bind = lambda _session: fake_repo
    monkeypatch.setattr(workspaces_router, "invitation_repository", fake_repo)

    r_409 = isolated_client.post(
        f"/v1/workspaces/{ws_id}/invitations",
        json={"email": "invitee@example.com", "role": "viewer"},
    )
    assert r_409.status_code == 409
    assert r_409.json()["detail"]["code"] == "pending_invitation_conflict"

    r_429 = isolated_client.post(f"/v1/workspaces/{ws_id}/invitations/{inv_id}/resend")
    assert r_429.status_code == 429
    assert r_429.headers.get("retry-after") == "42"
    assert r_429.json()["detail"]["code"] == "resend_cooldown_active"

    r_404 = isolated_client.post(f"/v1/workspaces/{ws_id}/invitations/{inv_id}/revoke")
    assert r_404.status_code == 404
    assert r_404.json()["detail"]["code"] == "invitation_not_found"
