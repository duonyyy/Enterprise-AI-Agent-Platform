"""Unit, Security, and Contract Validation Tests for Workspace Bootstrap & Discovery (Step 03-A / SPEC-WORKSPACE-001).

Covers:
- Clean architecture boundary checks (no FastAPI imports in domain/service/repository/dto layers).
- DTO strict validation (`extra="forbid"`, slug format, UUID validation, mass-assignment prevention).
- Authentication & CSRF enforcement on `/v1/workspaces` and `/v1/workspaces/{workspace_id}`.
- Token confinement (`workspace_token` blocked from `/v1/workspaces` collection and cross-workspace access).
- Provisioning authority (`WS-D01`):
  - Normal users and non-owner roles (`admin`, `developer`, `viewer`) denied `403`.
  - Cross-tenant owner denied `403`.
  - Non-operator specifying another `initial_owner_user_id` or bootstrapping a new tenant denied `403`.
  - Concurrent revocation during `FOR SHARE` lock denied `403`.
  - Authorized Bootstrap Operator and authorized Tenant Owner succeed (`201 Created`).
- Conflict handling (`409 Conflict` on duplicate slug) and fail-closed `503` on database errors without leaking internals.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.main import app
from control_api.modules.identity.repositories.session_repository import InMemoryRevocationStore
from control_api.modules.identity.services.session_service import session_manager
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
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


def test_clean_architecture_no_fastapi_in_workspaces_domain_service_repo() -> None:
    """Verify workspaces models, repositories, services, and DTOs do not import FastAPI."""
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
                        assert not alias.name.startswith("fastapi"), (
                            f"Forbidden FastAPI import in {py_file}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("fastapi"), (
                        f"Forbidden FastAPI import in {py_file}"
                    )


@pytest.fixture
def mock_ws_env():
    """Set up deterministic session manager and mock DB session matching exact repository SQL."""
    orig_denylist = session_manager.denylist
    orig_operators = list(settings.bootstrap_operator_subjects)
    session_manager.denylist = InMemoryRevocationStore()
    settings.bootstrap_operator_subjects = ["https://idp.enterprise.local|operator-sub-1"]

    state: dict[str, Any] = {
        "users": {},
        "tenants": {},
        "workspaces": {},
        "memberships": [],
        "audit_events": [],
        "revoke_on_lock": False,
        "raise_db_error": False,
    }

    session = AsyncMock(spec=AsyncSession)

    async def execute_side_effect(stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt).strip()
        params = params or {}
        res = MagicMock()

        if (
            state["raise_db_error"]
            and "iam.get_user_workspaces" not in sql
            and "FROM iam.users" not in sql
        ):
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("simulated secret password=supersecret host=10.0.0.99"),
            )

        if "SELECT id FROM iam.users WHERE id = :id" in sql or (
            "SELECT id, issuer, subject, display_name, email_normalized FROM iam.users WHERE id = :id" in sql
        ):
            uid = str(params.get("id"))
            u = state["users"].get(uid)
            res.mappings = lambda: _SyncMappings(item=u)
            return res

        if "SELECT * FROM iam.get_user_workspaces(:user_id)" in sql:
            uid = str(params.get("user_id"))
            items = [
                m
                for m in state["memberships"]
                if str(m["user_id"]) == uid
                and m.get("membership_status", "active") == "active"
                and m.get("workspace_status", "active") == "active"
                and m.get("tenant_status", "active") == "active"
            ]
            res.mappings = lambda: _SyncMappings(items=items)
            return res

        if "SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)" in sql:
            uid = str(params.get("user_id"))
            wid = str(params.get("workspace_id"))
            match = None
            for m in state["memberships"]:
                if (
                    str(m["user_id"]) == uid
                    and str(m["workspace_id"]) == wid
                    and m.get("membership_status", "active") == "active"
                    and m.get("workspace_status", "active") == "active"
                    and m.get("tenant_status", "active") == "active"
                ):
                    match = {
                        "tenant_id": m["tenant_id"],
                        "workspace_id": m["workspace_id"],
                        "role": m["role"],
                        "membership_status": "active",
                        "workspace_status": "active",
                        "tenant_status": "active",
                    }
                    break
            res.mappings = lambda: _SyncMappings(item=match)
            return res

        if "SET LOCAL app." in sql:
            return res

        if "FOR SHARE" in sql and "FROM iam.memberships AS m" in sql:
            if state["revoke_on_lock"]:
                res.mappings = lambda: _SyncMappings(item=None)
                return res
            uid = str(params.get("user_id"))
            tid = str(params.get("tenant_id"))
            wid = str(params.get("workspace_id"))
            found = None
            for m in state["memberships"]:
                if (
                    str(m["user_id"]) == uid
                    and str(m["tenant_id"]) == tid
                    and str(m["workspace_id"]) == wid
                    and m["role"] == "owner"
                    and m.get("membership_status", "active") == "active"
                    and m.get("workspace_status", "active") == "active"
                    and m.get("tenant_status", "active") == "active"
                ):
                    found = {"id": str(uuid4())}
                    break
            res.mappings = lambda: _SyncMappings(item=found)
            return res

        if "FROM iam.tenants" in sql and "FOR SHARE" in sql:
            tid = str(params.get("tenant_id"))
            t = state["tenants"].get(tid)
            if t and t.get("status", "active") == "active":
                res.mappings = lambda: _SyncMappings(item=t)
            else:
                res.mappings = lambda: _SyncMappings(item=None)
            return res

        if "INSERT INTO iam.tenants" in sql:
            tid = str(params["tenant_id"])
            slug = str(params["tenant_slug"])
            for existing_t in state["tenants"].values():
                if existing_t["slug"] == slug:
                    orig = Exception("duplicate key value violates unique constraint uq_tenants_slug")
                    orig.pgcode = "23505"  # type: ignore[attr-defined]
                    raise IntegrityError("INSERT INTO iam.tenants", params, orig)
            t_row = {
                "id": tid,
                "slug": slug,
                "name": str(params["tenant_name"]),
                "status": "active",
            }
            state["tenants"][tid] = t_row
            res.mappings = lambda: _SyncMappings(item=t_row)
            return res

        if "INSERT INTO iam.workspaces" in sql:
            wid = str(params["workspace_id"])
            tid = str(params["tenant_id"])
            slug = str(params["slug"])
            for existing_w in state["workspaces"].values():
                if str(existing_w["tenant_id"]) == tid and existing_w["slug"] == slug:
                    orig = Exception("duplicate key value violates unique constraint uq_workspaces_tenant_slug")
                    orig.pgcode = "23505"  # type: ignore[attr-defined]
                    raise IntegrityError("INSERT INTO iam.workspaces", params, orig)
            w_row = {
                "id": wid,
                "tenant_id": tid,
                "slug": slug,
                "name": str(params["name"]),
                "status": "active",
                "created_at": datetime.now(UTC),
            }
            state["workspaces"][wid] = w_row
            res.mappings = lambda: _SyncMappings(item=w_row)
            return res

        if "INSERT INTO iam.memberships" in sql:
            tid = str(params["tenant_id"])
            wid = str(params["workspace_id"])
            uid = str(params["owner_user_id"])
            t = state["tenants"][tid]
            w = state["workspaces"][wid]
            mem_id = str(uuid4())
            now_dt = datetime.now(UTC)
            state["memberships"].append(
                {
                    "id": mem_id,
                    "tenant_id": tid,
                    "tenant_slug": t["slug"],
                    "tenant_name": t["name"],
                    "workspace_id": wid,
                    "workspace_slug": w["slug"],
                    "workspace_name": w["name"],
                    "user_id": uid,
                    "role": "owner",
                    "membership_status": "active",
                    "workspace_status": "active",
                    "tenant_status": "active",
                }
            )
            res.mappings = lambda: _SyncMappings(
                item={
                    "id": mem_id,
                    "user_id": uid,
                    "role": "owner",
                    "status": "active",
                    "joined_at": now_dt,
                }
            )
            return res

        if "INSERT INTO governance.audit_events" in sql:
            audit_id = str(uuid4())
            now_dt = datetime.now(UTC)
            record = dict(params)
            record["id"] = audit_id
            record["occurred_at"] = now_dt
            state["audit_events"].append(record)
            res.mappings = lambda: _SyncMappings(item={"id": audit_id, "occurred_at": now_dt})
            return res

        if "FROM iam.workspaces AS w" in sql and "JOIN iam.tenants AS t" in sql:
            tid = str(params.get("tenant_id"))
            wid = str(params.get("workspace_id"))
            uid = str(params.get("user_id"))
            w = state["workspaces"].get(wid)
            t = state["tenants"].get(tid)
            mem = next(
                (
                    m
                    for m in state["memberships"]
                    if str(m["tenant_id"]) == tid
                    and str(m["workspace_id"]) == wid
                    and str(m["user_id"]) == uid
                    and m.get("membership_status", "active") == "active"
                ),
                None,
            )
            if w and t and mem and str(w["tenant_id"]) == tid:
                item = {
                    "workspace_id": w["id"],
                    "tenant_id": t["id"],
                    "tenant_slug": t["slug"],
                    "tenant_name": t["name"],
                    "workspace_slug": w["slug"],
                    "workspace_name": w["name"],
                    "status": w["status"],
                    "role": mem["role"],
                    "created_at": w["created_at"],
                }
                res.mappings = lambda: _SyncMappings(item=item)
            else:
                res.mappings = lambda: _SyncMappings(item=None)
            return res

        res.mappings = lambda: _SyncMappings(item=None, items=[])
        return res

    session.execute.side_effect = execute_side_effect

    async def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db

    try:
        yield state, session
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        session_manager.denylist = orig_denylist
        settings.bootstrap_operator_subjects = orig_operators


def _provision_mock_user(
    state: dict[str, Any],
    *,
    subject: str,
    issuer: str = "https://idp.enterprise.local",
    email: str = "user@enterprise.local",
) -> dict[str, str]:
    uid = str(uuid4())
    user = {
        "id": uid,
        "issuer": issuer,
        "subject": subject,
        "display_name": subject,
        "email_normalized": email,
    }
    state["users"][uid] = user
    return user


def test_workspace_dto_validation_and_mass_assignment_protection(mock_ws_env) -> None:
    """Verify request validation rejects invalid slugs, missing tenant targets, and forged fields."""
    state, _ = mock_ws_env
    op_user = _provision_mock_user(state, subject="operator-sub-1")
    _, token = session_manager.create_session(
        user_id=op_user["id"],
        issuer=op_user["issuer"],
        subject=op_user["subject"],
    )
    client = TestClient(app, base_url="http://localhost:8000")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Invalid workspace slug (uppercase / spaces / leading dash)
    for bad_slug in ["Invalid_Slug", "-leading-dash", "has space", ""]:
        res = client.post(
            "/v1/workspaces",
            json={
                "name": "Valid Name",
                "slug": bad_slug,
                "tenant_slug": "vinai",
                "tenant_name": "VinAI Enterprise",
            },
            headers=headers,
        )
        assert res.status_code == 422, f"Expected 422 for slug={bad_slug!r}, got {res.status_code}"

    # 2. Missing both tenant_id and tenant_slug, or partial tenant bootstrap fields
    res_no_tenant = client.post(
        "/v1/workspaces",
        json={"name": "Valid Name", "slug": "valid-slug"},
        headers=headers,
    )
    assert res_no_tenant.status_code == 422

    res_partial_tenant = client.post(
        "/v1/workspaces",
        json={"name": "Valid Name", "slug": "valid-slug", "tenant_slug": "vinai"},
        headers=headers,
    )
    assert res_partial_tenant.status_code == 422

    # 3. Invalid UUID in tenant_id or initial_owner_user_id
    res_bad_uuid = client.post(
        "/v1/workspaces",
        json={"name": "Valid Name", "slug": "valid-slug", "tenant_id": "not-a-uuid"},
        headers=headers,
    )
    assert res_bad_uuid.status_code == 422

    # 4. Mass assignment / forged fields rejected by extra="forbid"
    for forged_field in [
        {"role": "owner"},
        {"status": "active"},
        {"workspace_id": str(uuid4())},
        {"permissions": ["workspace:*"]},
        {"is_platform_admin": True},
    ]:
        payload = {
            "name": "Valid Name",
            "slug": "valid-slug",
            "tenant_slug": "vinai",
            "tenant_name": "VinAI Enterprise",
            **forged_field,
        }
        res_forged = client.post("/v1/workspaces", json=payload, headers=headers)
        assert res_forged.status_code == 422, f"Forged field {forged_field} was not rejected with 422"


def test_workspace_auth_csrf_and_token_confinement(mock_ws_env) -> None:
    """Verify unauthenticated (401), CSRF cross-origin (403), and scoped workspace token confinement (403)."""
    state, _ = mock_ws_env
    op_user = _provision_mock_user(state, subject="operator-sub-1")
    client = TestClient(app, base_url="http://localhost:8000")

    # 1. Unauthenticated requests -> 401
    assert client.get("/v1/workspaces").status_code == 401
    assert (
        client.post(
            "/v1/workspaces",
            json={"name": "WS", "slug": "ws", "tenant_slug": "t12", "tenant_name": "T12"},
        ).status_code
        == 401
    )
    assert client.get(f"/v1/workspaces/{uuid4()}").status_code == 401

    # 2. Cookie-authenticated POST /v1/workspaces with cross-site Sec-Fetch-Site or Evil Origin -> 403 CSRF
    _, session_token = session_manager.create_session(
        user_id=op_user["id"],
        issuer=op_user["issuer"],
        subject=op_user["subject"],
    )
    client.cookies.set(settings.session_cookie_name, session_token)
    res_csrf = client.post(
        "/v1/workspaces",
        json={"name": "WS", "slug": "ws", "tenant_slug": "t12", "tenant_name": "T12"},
        headers={"Origin": "https://evil.attacker.example"},
    )
    assert res_csrf.status_code == 403

    res_sec_fetch = client.post(
        "/v1/workspaces",
        json={"name": "WS", "slug": "ws", "tenant_slug": "t12", "tenant_name": "T12"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert res_sec_fetch.status_code == 403
    client.cookies.clear()

    # 3. Bootstrap a valid workspace and exchange session for a scoped workspace_token
    res_create = client.post(
        "/v1/workspaces",
        json={
            "name": "Core Workspace",
            "slug": "core-ws",
            "tenant_slug": "vinai",
            "tenant_name": "VinAI Enterprise",
        },
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert res_create.status_code == 201
    ws_data = res_create.json()
    ws_id = ws_data["workspace_id"]
    tid = ws_data["tenant_id"]

    res_exchange = client.post(
        "/v1/auth/token",
        json={"workspace_id": ws_id},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert res_exchange.status_code == 200
    ws_token = res_exchange.json()["access_token"]
    ws_headers = {"Authorization": f"Bearer {ws_token}"}

    # Scoped workspace token MUST NOT be allowed to list all workspaces or create a new workspace
    res_list_with_ws_token = client.get("/v1/workspaces", headers=ws_headers)
    assert res_list_with_ws_token.status_code == 403

    res_create_with_ws_token = client.post(
        "/v1/workspaces",
        json={"name": "Second WS", "slug": "second-ws", "tenant_id": tid},
        headers=ws_headers,
    )
    assert res_create_with_ws_token.status_code == 403

    # Scoped workspace token CAN read its own bound workspace, but NOT another workspace
    res_own_detail = client.get(f"/v1/workspaces/{ws_id}", headers=ws_headers)
    assert res_own_detail.status_code == 200
    assert res_own_detail.json()["workspace_id"] == ws_id
    assert res_own_detail.json()["workspace_slug"] == "core-ws"

    res_other_detail = client.get(f"/v1/workspaces/{uuid4()}", headers=ws_headers)
    assert res_other_detail.status_code == 403


def test_workspace_bootstrap_authority_matrix_ws_d01(mock_ws_env) -> None:
    """Verify WS-D01 provisioning authority rules across normal users, non-owners, owners, and operators."""
    state, _ = mock_ws_env
    op_user = _provision_mock_user(state, subject="operator-sub-1", email="op@vinai.io")
    normal_user = _provision_mock_user(state, subject="normal-user-1", email="normal@vinai.io")
    admin_user = _provision_mock_user(state, subject="admin-user-1", email="admin@vinai.io")
    designated_owner = _provision_mock_user(state, subject="designated-owner-1", email="owner@vinai.io")

    client = TestClient(app, base_url="http://localhost:8000")

    _, op_token = session_manager.create_session(
        user_id=op_user["id"], issuer=op_user["issuer"], subject=op_user["subject"]
    )
    _, normal_token = session_manager.create_session(
        user_id=normal_user["id"], issuer=normal_user["issuer"], subject=normal_user["subject"]
    )
    _, admin_token = session_manager.create_session(
        user_id=admin_user["id"], issuer=admin_user["issuer"], subject=admin_user["subject"]
    )
    _, owner_token = session_manager.create_session(
        user_id=designated_owner["id"], issuer=designated_owner["issuer"], subject=designated_owner["subject"]
    )

    # 1. Normal user with same email domain (@vinai.io) cannot bootstrap a tenant or workspace -> 403
    res_normal_bootstrap = client.post(
        "/v1/workspaces",
        json={
            "name": "Rogue WS",
            "slug": "rogue-ws",
            "tenant_slug": "vinai",
            "tenant_name": "VinAI Enterprise",
        },
        headers={"Authorization": f"Bearer {normal_token}"},
    )
    assert res_normal_bootstrap.status_code == 403
    assert len(state["workspaces"]) == 0

    # 2. Bootstrap Operator creates Tenant A + first Workspace, designating `designated_owner` as initial Owner
    res_op_create = client.post(
        "/v1/workspaces",
        json={
            "name": "Alpha Workspace",
            "slug": "alpha-ws",
            "tenant_slug": "tenant-alpha",
            "tenant_name": "Tenant Alpha",
            "initial_owner_user_id": designated_owner["id"],
        },
        headers={"Authorization": f"Bearer {op_token}"},
    )
    assert res_op_create.status_code == 201
    alpha_ws = res_op_create.json()
    tenant_alpha_id = alpha_ws["tenant_id"]
    assert alpha_ws["role"] == "owner"
    assert alpha_ws["workspace_slug"] == "alpha-ws"
    assert alpha_ws["initial_owner_user_id"] == designated_owner["id"]
    assert alpha_ws["audit_event_id"] is not None
    assert len(state["audit_events"]) == 1
    assert state["audit_events"][0]["action"] == "workspace.bootstrap"

    # 3. Normal user still cannot create a workspace in existing Tenant Alpha -> 403
    res_normal_in_alpha = client.post(
        "/v1/workspaces",
        json={"name": "Rogue 2", "slug": "rogue-2", "tenant_id": tenant_alpha_id},
        headers={"Authorization": f"Bearer {normal_token}"},
    )
    assert res_normal_in_alpha.status_code == 403

    # 4. User with `admin` role (not `owner`) in Tenant Alpha cannot create a workspace -> 403
    state["memberships"].append(
        {
            "tenant_id": tenant_alpha_id,
            "tenant_slug": "tenant-alpha",
            "tenant_name": "Tenant Alpha",
            "workspace_id": alpha_ws["workspace_id"],
            "workspace_slug": "alpha-ws",
            "workspace_name": "Alpha Workspace",
            "user_id": admin_user["id"],
            "role": "admin",
            "membership_status": "active",
            "workspace_status": "active",
            "tenant_status": "active",
        }
    )
    res_admin_create = client.post(
        "/v1/workspaces",
        json={"name": "Admin WS", "slug": "admin-ws", "tenant_id": tenant_alpha_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin_create.status_code == 403

    # 5. Designated Owner in Tenant Alpha CAN create a second workspace in Tenant Alpha
    res_owner_second = client.post(
        "/v1/workspaces",
        json={"name": "Beta Workspace", "slug": "beta-ws", "tenant_id": tenant_alpha_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res_owner_second.status_code == 201
    beta_ws = res_owner_second.json()
    assert beta_ws["tenant_id"] == tenant_alpha_id
    assert beta_ws["workspace_slug"] == "beta-ws"
    assert beta_ws["initial_owner_user_id"] == designated_owner["id"]
    assert state["audit_events"][1]["action"] == "workspace.create"

    # 6. Designated Owner in Tenant Alpha CANNOT create a new tenant or designate another owner -> 403
    res_owner_new_tenant = client.post(
        "/v1/workspaces",
        json={
            "name": "Gamma WS",
            "slug": "gamma-ws",
            "tenant_slug": "tenant-gamma",
            "tenant_name": "Tenant Gamma",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res_owner_new_tenant.status_code == 403

    res_owner_assign_other = client.post(
        "/v1/workspaces",
        json={
            "name": "Delta WS",
            "slug": "delta-ws",
            "tenant_id": tenant_alpha_id,
            "initial_owner_user_id": normal_user["id"],
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res_owner_assign_other.status_code == 403

    # 7. Concurrent revocation between discovery and `FOR SHARE` lock -> 403
    state["revoke_on_lock"] = True
    res_revoked_race = client.post(
        "/v1/workspaces",
        json={"name": "Race WS", "slug": "race-ws", "tenant_id": tenant_alpha_id},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert res_revoked_race.status_code == 403
    state["revoke_on_lock"] = False


def test_workspace_conflict_409_and_fail_closed_503(mock_ws_env) -> None:
    """Verify duplicate slug returns 409 Conflict and DB errors return 503 without leaking details."""
    state, _ = mock_ws_env
    op_user = _provision_mock_user(state, subject="operator-sub-1")
    _, op_token = session_manager.create_session(
        user_id=op_user["id"], issuer=op_user["issuer"], subject=op_user["subject"]
    )
    client = TestClient(app, base_url="http://localhost:8000")
    headers = {"Authorization": f"Bearer {op_token}"}

    # First creation succeeds
    res1 = client.post(
        "/v1/workspaces",
        json={
            "name": "Primary",
            "slug": "primary-ws",
            "tenant_slug": "vinai",
            "tenant_name": "VinAI Enterprise",
        },
        headers=headers,
    )
    assert res1.status_code == 201
    tid = res1.json()["tenant_id"]

    # Duplicate workspace slug in same tenant -> 409 Conflict
    res_dup_ws = client.post(
        "/v1/workspaces",
        json={"name": "Primary Duplicate", "slug": "primary-ws", "tenant_id": tid},
        headers=headers,
    )
    assert res_dup_ws.status_code == 409
    assert "conflict" in res_dup_ws.json()["detail"].lower() or "already exists" in res_dup_ws.json()["detail"].lower()

    # Duplicate tenant slug -> 409 Conflict
    res_dup_tenant = client.post(
        "/v1/workspaces",
        json={
            "name": "Other WS",
            "slug": "other-ws",
            "tenant_slug": "vinai",
            "tenant_name": "VinAI Duplicate",
        },
        headers=headers,
    )
    assert res_dup_tenant.status_code == 409

    # Simulated database failure -> 503 Service Unavailable with sanitized message
    state["raise_db_error"] = True
    res_503 = client.post(
        "/v1/workspaces",
        json={"name": "Outage WS", "slug": "outage-ws", "tenant_id": tid},
        headers=headers,
    )
    assert res_503.status_code == 503
    body_text = res_503.text.lower()
    assert "supersecret" not in body_text
    assert "10.0.0.99" not in body_text
