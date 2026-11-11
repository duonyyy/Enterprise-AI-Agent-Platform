"""Contract compatibility tests validating OpenAPI 3.1 schemas and runtime DTOs."""

from __future__ import annotations

from pathlib import Path

import yaml
from control_api.modules.identity.dto import (
    CallbackResponse,
    CurrentUserResponse,
    WorkspaceMembershipDTO,
)
from control_api.modules.workspaces.dto import (
    CreateWorkspaceRequestDTO,
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
    WorkspaceSummaryDTO,
)
from jsonschema import Draft202012Validator


def test_openapi_specs_existence():
    root = Path(__file__).resolve().parents[2]
    assert (root / "contracts" / "openapi" / "control-api.openapi.yaml").exists()
    assert (root / "contracts" / "openapi" / "runtime-api.openapi.yaml").exists()


def test_openapi_auth_schema_nullability_and_validation():
    """Verify OpenAPI 3.1 schemas correctly validate nullable string fields (Finding R09)."""
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    # 1. Check /v1/auth/callback schema
    callback_schema = spec["paths"]["/v1/auth/callback"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    validator_callback = Draft202012Validator(callback_schema)

    # Valid payload with null fields
    payload_with_null = {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "issuer": "http://localhost:8080/realms/enterprise",
        "subject": "alice",
        "display_name": None,
        "email_normalized": None,
        "redirect_url": "/login",
    }
    assert validator_callback.is_valid(payload_with_null), f"Errors: {list(validator_callback.iter_errors(payload_with_null))}"

    # DTO roundtrip
    dto_callback = CallbackResponse(
        user_id="00000000-0000-0000-0000-000000000001",
        issuer="http://localhost:8080/realms/enterprise",
        subject="alice",
        display_name=None,
        email_normalized=None,
        redirect_url="/login",
    )
    assert validator_callback.is_valid(dto_callback.model_dump())

    # 2. Check /v1/auth/me schema
    me_schema = spec["paths"]["/v1/auth/me"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    validator_me = Draft202012Validator(me_schema)

    dto_me = CurrentUserResponse(
        user_id="00000000-0000-0000-0000-000000000001",
        issuer="http://localhost:8080/realms/enterprise",
        subject="alice",
        display_name=None,
        email_normalized=None,
        workspaces=[
            WorkspaceMembershipDTO(
                tenant_id="00000000-0000-0000-0000-000000000002",
                tenant_slug="vinai",
                tenant_name="VinAI Enterprise",
                workspace_id="00000000-0000-0000-0000-000000000003",
                workspace_slug="default",
                workspace_name="Default Workspace",
                role="developer",
            )
        ],
    )
    assert validator_me.is_valid(dto_me.model_dump())


def test_openapi_security_schemes():
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    components = spec.get("components", {})
    sec_schemes = components.get("securitySchemes", {})
    assert "SessionCookie" in sec_schemes
    assert sec_schemes["SessionCookie"]["type"] == "apiKey"
    assert sec_schemes["SessionCookie"]["in"] == "cookie"
    assert "BearerAuth" in sec_schemes
    assert sec_schemes["BearerAuth"]["type"] == "http"
    assert sec_schemes["BearerAuth"]["scheme"] == "bearer"


def test_openapi_workspaces_bootstrap_and_discovery_contracts():
    """Verify /v1/workspaces and /v1/workspaces/{workspace_id} OpenAPI 3.1 schemas match runtime DTOs."""
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    ws_collection = spec["paths"]["/v1/workspaces"]
    ws_detail = spec["paths"]["/v1/workspaces/{workspace_id}"]

    # 1. POST /v1/workspaces request & 201 response
    create_req_schema = ws_collection["post"]["requestBody"]["content"]["application/json"]["schema"]
    create_resp_schema = ws_collection["post"]["responses"]["201"]["content"]["application/json"]["schema"]
    validator_create_req = Draft202012Validator(create_req_schema)
    validator_create_resp = Draft202012Validator(create_resp_schema)

    dto_req = CreateWorkspaceRequestDTO(
        name="Core Platform",
        slug="core-platform",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )
    assert validator_create_req.is_valid(dto_req.model_dump(exclude_none=True))

    # Unknown forged fields must be rejected by both OpenAPI schema and DTO
    forged_payload = {
        "name": "Core Platform",
        "slug": "core-platform",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "role": "owner",
    }
    assert not validator_create_req.is_valid(forged_payload)

    dto_resp = WorkspaceResponseDTO(
        workspace_id="00000000-0000-0000-0000-000000000010",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_slug="vinai",
        tenant_name="VinAI Enterprise",
        workspace_slug="core-platform",
        workspace_name="Core Platform",
        status="active",
        role="owner",
        initial_owner_user_id="00000000-0000-0000-0000-000000000099",
        audit_event_id="00000000-0000-0000-0000-000000000088",
        created_at="2026-11-12T00:00:00Z",
    )
    assert validator_create_resp.is_valid(dto_resp.model_dump())

    # 2. GET /v1/workspaces 200 response
    list_resp_schema = ws_collection["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    validator_list_resp = Draft202012Validator(list_resp_schema)
    dto_list = WorkspaceListResponseDTO(
        items=[
            WorkspaceSummaryDTO(
                workspace_id="00000000-0000-0000-0000-000000000010",
                tenant_id="00000000-0000-0000-0000-000000000001",
                tenant_slug="vinai",
                tenant_name="VinAI Enterprise",
                workspace_slug="core-platform",
                workspace_name="Core Platform",
                status="active",
                role="owner",
            )
        ]
    )
    assert validator_list_resp.is_valid(dto_list.model_dump())

    # 3. GET /v1/workspaces/{workspace_id} 200 response
    detail_resp_schema = ws_detail["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    validator_detail_resp = Draft202012Validator(detail_resp_schema)
    dto_detail = WorkspaceResponseDTO(
        workspace_id="00000000-0000-0000-0000-000000000010",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_slug="vinai",
        tenant_name="VinAI Enterprise",
        workspace_slug="core-platform",
        workspace_name="Core Platform",
        status="active",
        role="developer",
        initial_owner_user_id=None,
        audit_event_id=None,
        created_at="2026-11-12T00:00:00Z",
    )
    assert validator_detail_resp.is_valid(dto_detail.model_dump())

