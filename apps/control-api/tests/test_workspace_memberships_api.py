"""Unit and Security Tests for Scoped Workspace Member Management (ADR-021 / Step 03-D)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from control_api.core.auth import AuthenticatedUserContext, get_current_authenticated_user
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from control_api.modules.workspaces.models.rbac import WORKSPACE_ROLES
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


class _SyncMappings:
    def __init__(self, item: Any = None, items: list[Any] | None = None) -> None:
        self._item = item
        self._items = items or []

    def one_or_none(self) -> Any:
        return self._item

    def one(self) -> Any:
        return self._item

    def all(self) -> list[Any]:
        return self._items


def test_clean_architecture_no_fastapi_in_membership_layers() -> None:
    ws_root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "control_api"
        / "modules"
        / "workspaces"
    )
    for layer in ("models", "repositories", "services", "dto"):
        layer_dir = ws_root / layer
        for py_file in layer_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("fastapi")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("fastapi")


@pytest.fixture
def mock_member_env():
    orig_denylist = session_manager.denylist
    session_manager.denylist = InMemoryRevocationStore()

    tenant_id = uuid4()
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    owner_user_id = uuid4()
    owner2_user_id = uuid4()
    admin_user_id = uuid4()
    dev_user_id = uuid4()
    op_user_id = uuid4()
    viewer_user_id = uuid4()
    revoked_owner_user_id = uuid4()
    invited_user_id = uuid4()

    memberships: dict[str, dict[str, Any]] = {}

    def _add_member(
        user_id: Any,
        role: str,
        status: str,
        display_name: str,
        ws_id: Any = workspace_id,
    ) -> str:
        m_id = str(uuid4())
        memberships[m_id] = {
            "id": m_id,
            "tenant_id": str(tenant_id),
            "workspace_id": str(ws_id),
            "user_id": str(user_id),
            "role": role,
            "status": status,
            "display_name": display_name,
            "joined_at": datetime(2026, 11, 12, 10, len(memberships), tzinfo=UTC),
        }
        return m_id

    m_owner1 = _add_member(owner_user_id, "owner", "active", "Primary Owner")
    m_owner2 = _add_member(owner2_user_id, "owner", "active", "Second Owner")
    m_admin = _add_member(admin_user_id, "admin", "active", "Workspace Admin")
    m_dev = _add_member(dev_user_id, "developer", "active", "Developer User")
    m_op = _add_member(op_user_id, "operator", "active", "Operator User")
    m_viewer = _add_member(viewer_user_id, "viewer", "active", "Viewer User")
    m_revoked_owner = _add_member(revoked_owner_user_id, "owner", "revoked", "Revoked Owner")
    m_invited = _add_member(invited_user_id, "developer", "invited", "Invited Dev")
    m_other_ws = _add_member(uuid4(), "developer", "active", "Other WS Dev", ws_id=other_workspace_id)

    state: dict[str, Any] = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "other_workspace_id": other_workspace_id,
        "users": {
            "owner": owner_user_id,
            "owner2": owner2_user_id,
            "admin": admin_user_id,
            "developer": dev_user_id,
            "operator": op_user_id,
            "viewer": viewer_user_id,
        },
        "membership_ids": {
            "owner1": m_owner1,
            "owner2": m_owner2,
            "admin": m_admin,
            "developer": m_dev,
            "operator": m_op,
            "viewer": m_viewer,
            "revoked_owner": m_revoked_owner,
            "invited": m_invited,
            "other_ws": m_other_ws,
        },
        "memberships": memberships,
        "audit_events": [],
        "sql_trace": [],
        "raise_db_error": False,
        "workspace_status": "active",
        "tenant_status": "active",
    }

    session = AsyncMock(spec=AsyncSession)

    async def execute_side_effect(stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(stmt).strip().split())
        params = params or {}
        res = MagicMock()
        if state["raise_db_error"] and "set_config" not in sql:
            raise OperationalError(sql, params, Exception("connection lost"))

        state["sql_trace"].append(sql)

        if "set_config(" in sql:
            return res

        if "iam.resolve_user_workspace_membership(" in sql:
            uid = str(params["user_id"])
            wid = str(params["workspace_id"])
            found = next(
                (
                    m
                    for m in state["memberships"].values()
                    if m["user_id"] == uid and m["workspace_id"] == wid and m["status"] == "active"
                ),
                None,
            )
            if found is None or state["workspace_status"] != "active" or state["tenant_status"] != "active":
                res.mappings.return_value = _SyncMappings(item=None)
            else:
                res.mappings.return_value = _SyncMappings(
                    item={
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "role": found["role"],
                        "tenant_status": state["tenant_status"],
                        "workspace_status": state["workspace_status"],
                    }
                )
            return res

        if "FROM iam.workspaces AS w" in sql:
            wid = str(params["workspace_id"])
            tid = str(params["tenant_id"])
            if wid == str(workspace_id) and tid == str(tenant_id):
                res.mappings.return_value = _SyncMappings(
                    item={
                        "id": wid,
                        "workspace_status": state["workspace_status"],
                        "tenant_status": state["tenant_status"],
                    }
                )
            else:
                res.mappings.return_value = _SyncMappings(item=None)
            return res

        if "FOR UPDATE OF m" in sql:
            wid = str(params["workspace_id"])
            tid = str(params["tenant_id"])
            actor_uid = str(params["actor_user_id"])
            target_mid = str(params["target_membership_id"])
            rows = [
                dict(m)
                for m in state["memberships"].values()
                if m["tenant_id"] == tid
                and m["workspace_id"] == wid
                and (
                    m["id"] == target_mid
                    or m["user_id"] == actor_uid
                    or (m["role"] == "owner" and m["status"] == "active")
                )
            ]
            rows.sort(key=lambda r: (r["user_id"], r["id"]))
            res.mappings.return_value = _SyncMappings(items=rows)
            return res

        if "SELECT role, status FROM iam.memberships" in sql:
            uid = str(params["user_id"])
            wid = str(params["workspace_id"])
            found = next(
                (m for m in state["memberships"].values() if m["user_id"] == uid and m["workspace_id"] == wid),
                None,
            )
            res.mappings.return_value = _SyncMappings(item=found)
            return res

        if "FROM iam.memberships AS m LEFT JOIN iam.users AS u" in sql:
            wid = str(params["workspace_id"])
            tid = str(params["tenant_id"])
            status_filter = params.get("status_filter")
            rows = [
                dict(m)
                for m in state["memberships"].values()
                if m["tenant_id"] == tid
                and m["workspace_id"] == wid
                and (status_filter is None or m["status"] == status_filter)
            ]
            rows.sort(key=lambda r: (r["joined_at"], r["id"]))
            cursor_jt = params.get("cursor_joined_at")
            cursor_id = str(params["cursor_id"]) if params.get("cursor_id") else None
            if cursor_jt is not None and cursor_id is not None:
                rows = [
                    r for r in rows if (r["joined_at"] > cursor_jt or (r["joined_at"] == cursor_jt and r["id"] > cursor_id))
                ]
            fetch_limit = int(params.get("fetch_limit", 26))
            res.mappings.return_value = _SyncMappings(items=rows[:fetch_limit])
            return res

        if "SELECT display_name FROM iam.users WHERE id = :user_id" in sql:
            uid = str(params["user_id"])
            found = next((m for m in state["memberships"].values() if m["user_id"] == uid), None)
            res.mappings.return_value = _SyncMappings(
                item={"display_name": found["display_name"]} if found else None
            )
            return res

        if "UPDATE iam.memberships SET role = :new_role" in sql:
            mid = str(params["membership_id"])
            m = state["memberships"][mid]
            m["role"] = str(params["new_role"])
            res.mappings.return_value = _SyncMappings(item=dict(m))
            return res

        if "UPDATE iam.memberships SET status = 'revoked'" in sql:
            mid = str(params["membership_id"])
            m = state["memberships"][mid]
            m["status"] = "revoked"
            res.mappings.return_value = _SyncMappings(item=dict(m))
            return res

        if "INSERT INTO governance.audit_events" in sql:
            evt_id = str(uuid4())
            state["audit_events"].append(
                {
                    "id": evt_id,
                    "action": params["action"],
                    "resource_id": params["resource_id"],
                    "details": params["details_redacted"],
                }
            )
            res.mappings.return_value = _SyncMappings(item={"id": evt_id})
            return res

        raise AssertionError(f"Unexpected SQL executed: {sql}")

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db
    try:
        yield state
    finally:
        app.dependency_overrides.clear()
        session_manager.denylist = orig_denylist


def _issue_app_session_cookie(user_id: Any) -> str:
    _, session_cookie = session_manager.create_session(
        user_id=str(user_id),
        issuer="https://idp.enterprise.local",
        subject=f"sub-{user_id}",
    )
    return session_cookie


def test_member_management_auth_csrf_and_token_confinement(mock_member_env) -> None:
    ws_id = str(mock_member_env["workspace_id"])
    m_dev = mock_member_env["membership_ids"]["developer"]

    with TestClient(app) as client:
        # Unauthenticated -> 401
        r_unauth = client.get(f"/v1/workspaces/{ws_id}/memberships")
        assert r_unauth.status_code == 401

        # Scoped workspace token without application session -> 403 active_session_required
        app.dependency_overrides[get_current_authenticated_user] = lambda: AuthenticatedUserContext(
            user_id=str(mock_member_env["users"]["owner"]),
            session_data=None,
            issuer=settings.jwt_issuer,
            subject=f"sub-{mock_member_env['users']['owner']}",
        )
        try:
            r_scoped = client.get(f"/v1/workspaces/{ws_id}/memberships")
            assert r_scoped.status_code == 403
            assert r_scoped.json()["detail"]["code"] == "active_session_required"
        finally:
            app.dependency_overrides.pop(get_current_authenticated_user, None)

        # Invalid Origin header on PATCH / POST -> 403 CSRF denial
        cookie = _issue_app_session_cookie(mock_member_env["users"]["owner"])
        r_bad_origin = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{m_dev}/role",
            cookies={settings.session_cookie_name: cookie},
            headers={"Origin": "https://evil.attacker.example"},
            json={"role": "operator", "expected_role": "developer", "expected_status": "active"},
        )
        assert r_bad_origin.status_code == 403

        # Malformed UUIDs -> 422
        r_bad_id = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/not-a-uuid/role",
            cookies={settings.session_cookie_name: cookie},
            headers={"Origin": "http://localhost:3000"},
            json={"role": "operator", "expected_role": "developer", "expected_status": "active"},
        )
        assert r_bad_id.status_code == 422
        assert r_bad_id.json()["detail"]["code"] == "invalid_membership_id"


def test_list_memberships_privacy_pagination_and_role_permissions(mock_member_env) -> None:
    ws_id = str(mock_member_env["workspace_id"])
    with TestClient(app) as client:
        # Owner and Admin allowed; developer/operator/viewer denied 403
        for denied_role in ("developer", "operator", "viewer"):
            cookie = _issue_app_session_cookie(mock_member_env["users"][denied_role])
            r_denied = client.get(
                f"/v1/workspaces/{ws_id}/memberships",
                cookies={settings.session_cookie_name: cookie},
            )
            assert r_denied.status_code == 403

        admin_cookie = _issue_app_session_cookie(mock_member_env["users"]["admin"])
        r_page1 = client.get(
            f"/v1/workspaces/{ws_id}/memberships?limit=3",
            cookies={settings.session_cookie_name: admin_cookie},
        )
        assert r_page1.status_code == 200
        assert r_page1.headers.get("cache-control") == "no-store"
        body1 = r_page1.json()
        assert len(body1["items"]) == 3
        assert body1["next_cursor"] is not None

        # Verify no private user identity or cross-workspace record leaks
        for item in body1["items"]:
            assert set(item.keys()) == {"membership_id", "user_id", "display_name", "role", "status", "joined_at"}
            assert item["membership_id"] != mock_member_env["membership_ids"]["other_ws"]

        # Status filter + cursor pagination
        r_active = client.get(
            f"/v1/workspaces/{ws_id}/memberships?status=active&limit=50",
            cookies={settings.session_cookie_name: admin_cookie},
        )
        assert r_active.status_code == 200
        assert all(i["status"] == "active" for i in r_active.json()["items"])
        assert len(r_active.json()["items"]) == 6

        # Invalid status -> 422; invalid cursor -> 422
        assert (
            client.get(
                f"/v1/workspaces/{ws_id}/memberships?status=archived",
                cookies={settings.session_cookie_name: admin_cookie},
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/v1/workspaces/{ws_id}/memberships?cursor=not-valid-base64",
                cookies={settings.session_cookie_name: admin_cookie},
            ).status_code
            == 422
        )


def test_role_matrix_promotion_demotion_lock_order_and_conflicts(mock_member_env) -> None:
    ws_id = str(mock_member_env["workspace_id"])
    mids = mock_member_env["membership_ids"]

    with TestClient(app) as client:
        owner_cookie = _issue_app_session_cookie(mock_member_env["users"]["owner"])
        admin_cookie = _issue_app_session_cookie(mock_member_env["users"]["admin"])
        headers = {"Origin": "http://localhost:3000"}

        # 1. Admin CANNOT manage Owner or Admin, CANNOT self-promote or promote others to Admin/Owner
        for protected_mid, protected_role in ((mids["owner1"], "owner"), (mids["admin"], "admin")):
            r_patch = client.patch(
                f"/v1/workspaces/{ws_id}/memberships/{protected_mid}/role",
                cookies={settings.session_cookie_name: admin_cookie},
                headers=headers,
                json={"role": "developer", "expected_role": protected_role, "expected_status": "active"},
            )
            assert r_patch.status_code == 403
            r_rev = client.post(
                f"/v1/workspaces/{ws_id}/memberships/{protected_mid}/revoke",
                cookies={settings.session_cookie_name: admin_cookie},
                headers=headers,
                json={"expected_role": protected_role, "expected_status": "active"},
            )
            assert r_rev.status_code == 403

        for target_up in ("admin", "owner"):
            r_up = client.patch(
                f"/v1/workspaces/{ws_id}/memberships/{mids['developer']}/role",
                cookies={settings.session_cookie_name: admin_cookie},
                headers=headers,
                json={"role": target_up, "expected_role": "developer", "expected_status": "active"},
            )
            assert r_up.status_code == 403

        # 2. Admin CAN manage developer -> operator and revoke viewer
        r_admin_ok = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['developer']}/role",
            cookies={settings.session_cookie_name: admin_cookie},
            headers=headers,
            json={"role": "operator", "expected_role": "developer", "expected_status": "active"},
        )
        assert r_admin_ok.status_code == 200
        assert r_admin_ok.json()["role"] == "operator"
        assert r_admin_ok.json()["outcome"] == "updated"
        assert r_admin_ok.json()["audit_event_id"] is not None

        # Verify workspace-first lock order (`FOR UPDATE OF w` before `ORDER BY m.user_id ASC, m.id ASC FOR UPDATE OF m`)
        trace = mock_member_env["sql_trace"]
        ws_lock_idx = next(i for i, q in enumerate(trace) if "FOR UPDATE OF w" in q)
        m_lock_idx = next(i for i, q in enumerate(trace) if "ORDER BY m.user_id ASC, m.id ASC FOR UPDATE OF m" in q)
        assert ws_lock_idx < m_lock_idx

        # 3. Retry with stale expected_role -> 409 stale_state (no audit added)
        audit_count_before = len(mock_member_env["audit_events"])
        r_stale = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['developer']}/role",
            cookies={settings.session_cookie_name: admin_cookie},
            headers=headers,
            json={"role": "viewer", "expected_role": "developer", "expected_status": "active"},
        )
        assert r_stale.status_code == 409
        assert r_stale.json()["detail"]["code"] == "stale_state"
        assert len(mock_member_env["audit_events"]) == audit_count_before

        # 4. Same role (no-op) -> 200 outcome='no_op', audit_event_id=None, no fake audit
        r_noop = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['developer']}/role",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"role": "operator", "expected_role": "operator", "expected_status": "active"},
        )
        assert r_noop.status_code == 200
        assert r_noop.json()["outcome"] == "no_op"
        assert r_noop.json()["audit_event_id"] is None
        assert len(mock_member_env["audit_events"]) == audit_count_before

        # 5. Owner CAN promote active member to Owner
        r_promote = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['admin']}/role",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"role": "owner", "expected_role": "admin", "expected_status": "active"},
        )
        assert r_promote.status_code == 200
        assert r_promote.json()["role"] == "owner"

        # 6. Cross-workspace / missing membership_id -> 404
        r_other = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['other_ws']}/role",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"role": "viewer", "expected_role": "developer", "expected_status": "active"},
        )
        assert r_other.status_code == 404
        assert r_other.json()["detail"]["code"] == "membership_not_found"


def test_last_owner_protection_and_revoke_idempotency(mock_member_env) -> None:
    ws_id = str(mock_member_env["workspace_id"])
    mids = mock_member_env["membership_ids"]
    headers = {"Origin": "http://localhost:3000"}

    with TestClient(app) as client:
        owner_cookie = _issue_app_session_cookie(mock_member_env["users"]["owner"])

        # Workspace starts with 2 active owners (owner1, owner2) and 1 revoked owner.
        # Demoting owner2 succeeds because owner1 remains active.
        r_demote2 = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['owner2']}/role",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
        )
        assert r_demote2.status_code == 200

        # Now owner1 is the LAST active owner (revoked_owner does NOT count!).
        # Neither self-demote nor self-revoke is permitted.
        r_last_demote = client.patch(
            f"/v1/workspaces/{ws_id}/memberships/{mids['owner1']}/role",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"role": "admin", "expected_role": "owner", "expected_status": "active"},
        )
        assert r_last_demote.status_code == 409
        assert r_last_demote.json()["detail"]["code"] == "last_owner_protected"

        r_last_revoke = client.post(
            f"/v1/workspaces/{ws_id}/memberships/{mids['owner1']}/revoke",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"expected_role": "owner", "expected_status": "active"},
        )
        assert r_last_revoke.status_code == 409
        assert r_last_revoke.json()["detail"]["code"] == "last_owner_protected"

        # Invited or revoked targets cannot be role-updated (no implicit restore)
        for non_active_mid, role in ((mids["invited"], "developer"), (mids["revoked_owner"], "owner")):
            r_non_active = client.patch(
                f"/v1/workspaces/{ws_id}/memberships/{non_active_mid}/role",
                cookies={settings.session_cookie_name: owner_cookie},
                headers=headers,
                json={"role": "viewer", "expected_role": role, "expected_status": "active"},
            )
            assert r_non_active.status_code == 409
            assert r_non_active.json()["detail"]["code"] == "membership_conflict"

        # Invited membership cannot be revoked via memberships endpoint (must use invitations)
        r_inv_rev = client.post(
            f"/v1/workspaces/{ws_id}/memberships/{mids['invited']}/revoke",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"expected_role": "developer", "expected_status": "active"},
        )
        assert r_inv_rev.status_code == 409
        assert r_inv_rev.json()["detail"]["code"] == "membership_conflict"

        # Revoke active viewer -> 200 revoked + audit; replay with matching expected_role -> 200 already_revoked, NO duplicate audit
        audit_before = len(mock_member_env["audit_events"])
        r_rev1 = client.post(
            f"/v1/workspaces/{ws_id}/memberships/{mids['viewer']}/revoke",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"expected_role": "viewer", "expected_status": "active"},
        )
        assert r_rev1.status_code == 200
        assert r_rev1.json()["status"] == "revoked"
        assert r_rev1.json()["outcome"] == "revoked"
        assert len(mock_member_env["audit_events"]) == audit_before + 1

        r_rev2 = client.post(
            f"/v1/workspaces/{ws_id}/memberships/{mids['viewer']}/revoke",
            cookies={settings.session_cookie_name: owner_cookie},
            headers=headers,
            json={"expected_role": "viewer", "expected_status": "active"},
        )
        assert r_rev2.status_code == 200
        assert r_rev2.json()["status"] == "revoked"
        assert r_rev2.json()["outcome"] == "already_revoked"
        assert r_rev2.json()["audit_event_id"] is None
        assert len(mock_member_env["audit_events"]) == audit_before + 1

        # Fail-closed 503 on database error
        mock_member_env["raise_db_error"] = True
        r_503 = client.get(
            f"/v1/workspaces/{ws_id}/memberships",
            cookies={settings.session_cookie_name: owner_cookie},
        )
        assert r_503.status_code == 503
        assert "connection lost" not in r_503.text


def test_role_constant_completeness() -> None:
    assert WORKSPACE_ROLES == ("owner", "admin", "developer", "operator", "viewer")
