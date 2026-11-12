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
    CreateInvitationRequestDTO,
    CreateWorkspaceRequestDTO,
    InvitationAcceptResponseDTO,
    InvitationHandoffRequestDTO,
    InvitationHandoffResponseDTO,
    InvitationPreviewResponseDTO,
    WorkspaceInvitationListResponseDTO,
    WorkspaceInvitationResponseDTO,
    WorkspaceListResponseDTO,
    WorkspaceResponseDTO,
    WorkspaceSummaryDTO,
)
from jsonschema import Draft202012Validator
from platform_common.invitations import InvitationDeliveryRequestedV1


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


def test_openapi_workspace_invitations_and_outbox_contracts():
    """Verify invitation OpenAPI 3.1 endpoints, error envelopes, and outbox JSON Schema."""
    import json

    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    inv_col = spec["paths"]["/v1/workspaces/{workspace_id}/invitations"]
    inv_resend = spec["paths"]["/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend"]
    inv_revoke = spec["paths"]["/v1/workspaces/{workspace_id}/invitations/{invitation_id}/revoke"]

    req_validator = Draft202012Validator(
        inv_col["post"]["requestBody"]["content"]["application/json"]["schema"]
    )
    create_resp_validator = Draft202012Validator(
        inv_col["post"]["responses"]["201"]["content"]["application/json"]["schema"]
    )
    list_resp_validator = Draft202012Validator(
        inv_col["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    )
    resend_resp_validator = Draft202012Validator(
        inv_resend["post"]["responses"]["202"]["content"]["application/json"]["schema"]
    )
    revoke_resp_validator = Draft202012Validator(
        inv_revoke["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    )
    err_validator = Draft202012Validator(
        inv_col["post"]["responses"]["409"]["content"]["application/json"]["schema"]
    )

    dto_req = CreateInvitationRequestDTO(email="Invitee+Tag@Example.COM", role="developer")
    assert req_validator.is_valid(dto_req.model_dump())
    assert not req_validator.is_valid({"email": "x@example.com", "role": "owner"})
    assert not req_validator.is_valid({"email": "x@example.com", "role": "viewer", "extra": 1})

    dto_inv = WorkspaceInvitationResponseDTO(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000010",
        recipient_email_normalized="invitee+tag@example.com",
        recipient_issuer="http://localhost:8080/realms/enterprise",
        role="developer",
        status="pending",
        generation=1,
        delivery_state="queued",
        delivery_error_code=None,
        created_by_user_id="00000000-0000-0000-0000-000000000099",
        delivery_requested_by_user_id="00000000-0000-0000-0000-000000000099",
        revoked_by_user_id=None,
        accepted_by_user_id=None,
        expires_at="2026-11-15T00:00:00Z",
        created_at="2026-11-12T00:00:00Z",
        updated_at="2026-11-12T00:00:00Z",
        provider_accepted_at=None,
        revoked_at=None,
        accepted_at=None,
        audit_event_id="00000000-0000-0000-0000-000000000201",
        outbox_event_id="00000000-0000-0000-0000-000000000301",
    )
    dumped = dto_inv.model_dump()
    assert create_resp_validator.is_valid(dumped)
    assert resend_resp_validator.is_valid(dumped)
    assert revoke_resp_validator.is_valid(dumped)
    assert not create_resp_validator.is_valid({**dumped, "proof": "secret"})

    dto_list = WorkspaceInvitationListResponseDTO(items=[dto_inv], next_cursor="opaque-cursor")
    assert list_resp_validator.is_valid(dto_list.model_dump())
    assert err_validator.is_valid({"detail": {"code": "pending_invitation_conflict", "message": "Duplicate"}})

    schema_path = root / "contracts" / "jsonschema" / "workspace-invitation-delivery-requested.v1.schema.json"
    outbox_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    outbox_validator = Draft202012Validator(outbox_schema)
    evt = InvitationDeliveryRequestedV1(
        invitation_id="00000000-0000-0000-0000-000000000101",
        generation=1,
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000010",
        delivery_requested_by_user_id="00000000-0000-0000-0000-000000000099",
        requested_at="2026-11-12T00:00:00Z",
    )
    evt_json = evt.model_dump(mode="json")
    assert outbox_validator.is_valid(evt_json)
    assert not outbox_validator.is_valid({**evt_json, "proof": "raw-secret"})
    assert not outbox_validator.is_valid({**evt_json, "recipient_email": "alice@example.com"})


def test_openapi_invitation_acceptance_schemas():
    root = Path(__file__).resolve().parents[2]
    spec = yaml.safe_load(
        (root / "contracts" / "openapi" / "control-api.openapi.yaml").read_text(encoding="utf-8")
    )

    sec_schemes = spec["components"]["securitySchemes"]
    assert "HandoffCookie" in sec_schemes
    assert sec_schemes["HandoffCookie"]["name"] == "agent_invite_handoff"

    handoff_op = spec["paths"]["/v1/invitations/{invitation_id}/handoff"]["post"]
    handoff_req_val = Draft202012Validator(
        handoff_op["requestBody"]["content"]["application/json"]["schema"]
    )
    handoff_resp_val = Draft202012Validator(
        handoff_op["responses"]["201"]["content"]["application/json"]["schema"]
    )
    handoff_req = InvitationHandoffRequestDTO(token="opaque-proof-token-123456")
    assert handoff_req_val.is_valid(handoff_req.model_dump())
    assert not handoff_req_val.is_valid({"token": "opaque-proof-token-123456", "role": "owner"})
    handoff_resp = InvitationHandoffResponseDTO(expires_at="2026-11-12T12:10:00Z")
    assert handoff_resp_val.is_valid(handoff_resp.model_dump())

    preview_op = spec["paths"]["/v1/invitations/{invitation_id}/preview"]["post"]
    preview_resp_val = Draft202012Validator(
        preview_op["responses"]["200"]["content"]["application/json"]["schema"]
    )
    preview_resp = InvitationPreviewResponseDTO(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_slug="vinai",
        tenant_name="VinAI Enterprise",
        workspace_id="00000000-0000-0000-0000-000000000010",
        workspace_slug="alpha-ws",
        workspace_name="Alpha Workspace",
        role="developer",
        status="pending",
        expires_at="2026-11-15T12:00:00Z",
        can_accept=True,
        acceptance_state="ready",
    )
    assert preview_resp_val.is_valid(preview_resp.model_dump())
    assert not preview_resp_val.is_valid({**preview_resp.model_dump(), "proof": "leak"})

    accept_op = spec["paths"]["/v1/invitations/{invitation_id}/accept"]["post"]
    accept_resp_val = Draft202012Validator(
        accept_op["responses"]["200"]["content"]["application/json"]["schema"]
    )
    accept_resp = InvitationAcceptResponseDTO(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000010",
        workspace_slug="alpha-ws",
        workspace_name="Alpha Workspace",
        role="developer",
        acceptance_state="accepted",
        redirect_url="/w/00000000-0000-0000-0000-000000000010/agents",
    )
    assert accept_resp_val.is_valid(accept_resp.model_dump())

