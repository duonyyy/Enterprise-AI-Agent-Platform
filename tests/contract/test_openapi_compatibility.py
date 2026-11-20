"""Contract compatibility tests validating OpenAPI 3.1 schemas and runtime DTOs."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
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
    RevokeMembershipRequestDTO,
    UpdateMembershipRoleRequestDTO,
    WorkspaceInvitationListResponseDTO,
    WorkspaceInvitationResponseDTO,
    WorkspaceListResponseDTO,
    WorkspaceMembershipListResponseDTO,
    WorkspaceMembershipRecordDTO,
    WorkspaceMembershipRevokeResponseDTO,
    WorkspaceMembershipRoleUpdateResponseDTO,
    WorkspaceResponseDTO,
    WorkspaceSummaryDTO,
)
from control_api.modules.workspaces.models.rbac import (
    WORKSPACE_ROLES,
    can_assign_target_role,
    can_list_workspace_members,
    can_manage_target_role,
    can_revoke_target_role,
)
from jsonschema import Draft202012Validator
from platform_common.invitations import InvitationDeliveryRequestedV1
from pydantic import ValidationError


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
    callback_schema = spec["paths"]["/v1/auth/callback"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
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
    assert validator_callback.is_valid(payload_with_null), (
        f"Errors: {list(validator_callback.iter_errors(payload_with_null))}"
    )

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
    me_schema = spec["paths"]["/v1/auth/me"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
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
    create_req_schema = ws_collection["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    create_resp_schema = ws_collection["post"]["responses"]["201"]["content"]["application/json"][
        "schema"
    ]
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
    list_resp_schema = ws_collection["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
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
    detail_resp_schema = ws_detail["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
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
    assert err_validator.is_valid(
        {"detail": {"code": "pending_invitation_conflict", "message": "Duplicate"}}
    )

    schema_path = (
        root / "contracts" / "jsonschema" / "workspace-invitation-delivery-requested.v1.schema.json"
    )
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

    # Promoted Owner is valid ONLY in already_accepted receipts, never in ready/accepted.
    promoted_preview = InvitationPreviewResponseDTO(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        tenant_slug="vinai",
        tenant_name="VinAI Enterprise",
        workspace_id="00000000-0000-0000-0000-000000000010",
        workspace_slug="alpha-ws",
        workspace_name="Alpha Workspace",
        role="owner",
        status="accepted",
        expires_at="2026-11-15T12:00:00Z",
        can_accept=False,
        acceptance_state="already_accepted",
    )
    assert preview_resp_val.is_valid(promoted_preview.model_dump())
    assert not preview_resp_val.is_valid({**preview_resp.model_dump(), "role": "owner"})
    with pytest.raises(ValidationError):
        InvitationPreviewResponseDTO(
            invitation_id="00000000-0000-0000-0000-000000000101",
            tenant_id="00000000-0000-0000-0000-000000000001",
            tenant_slug="vinai",
            tenant_name="VinAI Enterprise",
            workspace_id="00000000-0000-0000-0000-000000000010",
            workspace_slug="alpha-ws",
            workspace_name="Alpha Workspace",
            role="owner",
            status="pending",
            expires_at="2026-11-15T12:00:00Z",
            can_accept=True,
            acceptance_state="ready",
        )

    promoted_accept = InvitationAcceptResponseDTO(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000010",
        workspace_slug="alpha-ws",
        workspace_name="Alpha Workspace",
        role="owner",
        acceptance_state="already_accepted",
        redirect_url="/w/00000000-0000-0000-0000-000000000010/agents",
    )
    assert accept_resp_val.is_valid(promoted_accept.model_dump())
    assert not accept_resp_val.is_valid({**accept_resp.model_dump(), "role": "owner"})
    with pytest.raises(ValidationError):
        InvitationAcceptResponseDTO(
            invitation_id="00000000-0000-0000-0000-000000000101",
            tenant_id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000010",
            workspace_slug="alpha-ws",
            workspace_name="Alpha Workspace",
            role="owner",
            acceptance_state="accepted",
            redirect_url="/w/00000000-0000-0000-0000-000000000010/agents",
        )


def test_openapi_workspace_membership_management_schemas_and_rbac_matrix():
    root = Path(__file__).resolve().parents[2]
    spec = yaml.safe_load(
        (root / "contracts" / "openapi" / "control-api.openapi.yaml").read_text(encoding="utf-8")
    )

    memberships_path = spec["paths"]["/v1/workspaces/{workspace_id}/memberships"]
    get_op = memberships_path["get"]
    params_by_name = {param["name"]: param for param in get_op["parameters"]}
    assert params_by_name["limit"]["schema"]["maximum"] == 100
    assert params_by_name["limit"]["schema"]["default"] == 25
    assert params_by_name["status"]["schema"]["enum"] == ["active", "invited", "revoked"]

    list_val = Draft202012Validator(
        get_op["responses"]["200"]["content"]["application/json"]["schema"]
    )
    sample_member = WorkspaceMembershipRecordDTO(
        membership_id="00000000-0000-0000-0000-000000000201",
        user_id="00000000-0000-0000-0000-000000000301",
        display_name="Alice",
        role="developer",
        status="active",
        joined_at="2026-11-12T00:00:00Z",
    )
    list_dto = WorkspaceMembershipListResponseDTO(items=[sample_member], next_cursor=None)
    assert list_val.is_valid(list_dto.model_dump())
    assert not list_val.is_valid(
        {
            "items": [{**sample_member.model_dump(), "email_normalized": "alice@example.com"}],
            "next_cursor": None,
        }
    )

    role_op = spec["paths"]["/v1/workspaces/{workspace_id}/memberships/{membership_id}/role"][
        "patch"
    ]
    role_req_val = Draft202012Validator(
        role_op["requestBody"]["content"]["application/json"]["schema"]
    )
    role_resp_val = Draft202012Validator(
        role_op["responses"]["200"]["content"]["application/json"]["schema"]
    )
    role_req = UpdateMembershipRoleRequestDTO(
        role="owner",
        expected_role="developer",
        expected_status="active",
    )
    assert role_req_val.is_valid(role_req.model_dump())
    assert not role_req_val.is_valid(
        {**role_req.model_dump(), "tenant_id": "00000000-0000-0000-0000-000000000001"}
    )
    role_resp = WorkspaceMembershipRoleUpdateResponseDTO(
        **{**sample_member.model_dump(), "role": "owner"},
        outcome="updated",
        audit_event_id="00000000-0000-0000-0000-000000000401",
    )
    assert role_resp_val.is_valid(role_resp.model_dump())

    revoke_op = spec["paths"]["/v1/workspaces/{workspace_id}/memberships/{membership_id}/revoke"][
        "post"
    ]
    revoke_req_val = Draft202012Validator(
        revoke_op["requestBody"]["content"]["application/json"]["schema"]
    )
    revoke_resp_val = Draft202012Validator(
        revoke_op["responses"]["200"]["content"]["application/json"]["schema"]
    )
    revoke_req = RevokeMembershipRequestDTO(
        expected_role="developer",
        expected_status="active",
    )
    assert revoke_req_val.is_valid(revoke_req.model_dump())
    assert not revoke_req_val.is_valid({**revoke_req.model_dump(), "expected_status": "revoked"})
    revoke_resp = WorkspaceMembershipRevokeResponseDTO(
        **{**sample_member.model_dump(), "status": "revoked"},
        outcome="revoked",
        audit_event_id="00000000-0000-0000-0000-000000000402",
    )
    assert revoke_resp_val.is_valid(revoke_resp.model_dump())

    # Canonical RBAC matrix assertions
    assert can_list_workspace_members("owner")
    assert can_list_workspace_members("admin")
    assert not can_list_workspace_members("developer")
    assert not can_list_workspace_members("operator")
    assert not can_list_workspace_members("viewer")

    for target_role in WORKSPACE_ROLES:
        assert can_manage_target_role("owner", target_role)
        assert can_revoke_target_role("owner", target_role)
        for next_role in WORKSPACE_ROLES:
            assert can_assign_target_role("owner", target_role, next_role)

    for forbidden_target in ("owner", "admin"):
        assert not can_manage_target_role("admin", forbidden_target)
        assert not can_revoke_target_role("admin", forbidden_target)
        for next_role in WORKSPACE_ROLES:
            assert not can_assign_target_role("admin", forbidden_target, next_role)
    for managed_target in ("developer", "operator", "viewer"):
        assert can_manage_target_role("admin", managed_target)
        assert can_revoke_target_role("admin", managed_target)
        for elevated in ("owner", "admin"):
            assert not can_assign_target_role("admin", managed_target, elevated)
        for allowed_next in ("developer", "operator", "viewer"):
            assert can_assign_target_role("admin", managed_target, allowed_next)


def test_agent_draft_openapi_schemas_and_dto_compatibility():
    """Verify OpenAPI 3.1 canonical schemas for Step 04-A Agent drafts match runtime DTOs."""
    from control_api.modules.agents.dto.requests import (
        CreateAgentDraftDTO,
        PublishAgentRequestDTO,
        ReplaceAgentDraftSpecDTO,
        ValidateAgentDraftDTO,
    )
    from control_api.modules.agents.dto.responses import (
        AgentDiagnosticDTO,
        AgentDraftMetadataDTO,
        AgentDraftPageDTO,
        AgentErrorDetailDTO,
        AgentErrorResponseDTO,
        AgentValidationResultDTO,
        PublishResponseDTO,
    )

    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    # Resolve $ref helper for local OpenAPI components
    def resolve_component_schema(schema_obj: dict) -> dict:
        if "$ref" in schema_obj:
            ref_str = schema_obj["$ref"]
            if ref_str.startswith("#/components/schemas/"):
                schema_name = ref_str.split("/")[-1]
                target = spec["components"]["schemas"][schema_name]
                if "$ref" in target and target["$ref"].endswith(".json"):
                    # External jsonschema ref (e.g. AgentSpec)
                    ref_path = (spec_path.parent / target["$ref"]).resolve()
                    return json.loads(ref_path.read_text(encoding="utf-8"))
                return target
        return schema_obj

    # 1. Path definitions
    agents_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents"]
    assert "get" in agents_path
    assert "post" in agents_path
    assert agents_path["get"]["operationId"] == "listAgentDrafts"
    assert agents_path["post"]["operationId"] == "createAgentDraft"

    agent_detail_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}"]
    assert "get" in agent_detail_path
    assert "patch" in agent_detail_path
    assert agent_detail_path["get"]["operationId"] == "getAgentDraft"
    assert agent_detail_path["patch"]["operationId"] == "replaceAgentDraftSpec"

    # Mandatory precondition responses on PATCH
    patch_responses = agent_detail_path["patch"]["responses"]
    for expected_code in ("200", "400", "401", "403", "404", "409", "412", "422", "428", "503"):
        assert expected_code in patch_responses, f"Missing {expected_code} in PATCH responses"

    # ETag headers
    assert "ETag" in agents_path["post"]["responses"]["201"]["headers"]
    assert "ETag" in agent_detail_path["get"]["responses"]["200"]["headers"]
    assert "ETag" in agent_detail_path["patch"]["responses"]["200"]["headers"]

    # 2. DTO <-> Schema validation
    valid_spec = {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "knowledge-assistant"},
        "mode": "fixed_plan",
        "modelRef": "approved-model-v1",
        "persona": {"instructions": "Answer concisely using authorized sources."},
        "toolRefs": [],
        "skillRefs": [],
        "knowledgeRefs": [],
        "limits": {"maxSteps": 10},
    }

    # CreateAgentDraft
    create_dto = CreateAgentDraftDTO(
        slug="knowledge-assistant",
        display_name="Knowledge Assistant",
        spec=valid_spec,
    )
    agent_spec_schema = json.loads(
        (root / "contracts/jsonschema/agent-spec.v1.schema.json").read_text(encoding="utf-8")
    )
    create_schema = spec["components"]["schemas"]["CreateAgentDraft"]
    resolved_create_schema = copy.deepcopy(create_schema)
    resolved_create_schema["properties"]["spec"] = agent_spec_schema
    create_val = Draft202012Validator(resolved_create_schema)
    assert create_val.is_valid(create_dto.model_dump())
    assert not create_val.is_valid({**create_dto.model_dump(), "slug": "Invalid Slug!"})

    # ReplaceAgentDraftSpec
    replace_dto = ReplaceAgentDraftSpecDTO(spec=valid_spec)
    replace_schema = spec["components"]["schemas"]["ReplaceAgentDraftSpec"]
    resolved_replace_schema = copy.deepcopy(replace_schema)
    resolved_replace_schema["properties"]["spec"] = agent_spec_schema
    replace_val = Draft202012Validator(resolved_replace_schema)
    assert replace_val.is_valid(replace_dto.model_dump())
    assert not replace_val.is_valid({"extra_field": 123})

    # AgentDraftMetadata
    meta_dto = AgentDraftMetadataDTO(
        agent_id="00000000-0000-0000-0000-000000000001",
        slug="knowledge-assistant",
        display_name="Knowledge Assistant",
        revision=1,
        archived_at=None,
        created_at="2026-11-16T00:00:00Z",
    )
    meta_schema = spec["components"]["schemas"]["AgentDraftMetadata"]
    meta_val = Draft202012Validator(meta_schema)
    assert meta_val.is_valid(meta_dto.model_dump())

    # AgentDraftPage
    page_dto = AgentDraftPageDTO(items=[meta_dto], next_cursor=None)
    # Resolve items ref
    resolved_page_schema = copy.deepcopy(spec["components"]["schemas"]["AgentDraftPage"])
    resolved_page_schema["properties"]["items"]["items"] = meta_schema
    page_val = Draft202012Validator(resolved_page_schema)
    assert page_val.is_valid(page_dto.model_dump())

    # AgentError
    err_dto = AgentErrorResponseDTO(
        detail=AgentErrorDetailDTO(code="stale_revision", message="Draft revision is stale")
    )
    err_schema = spec["components"]["schemas"]["AgentError"]
    err_val = Draft202012Validator(err_schema)
    assert err_val.is_valid(err_dto.model_dump())

    # Step 04-B: Validate endpoint and DTOs
    validate_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/validate"]
    assert "post" in validate_path
    assert validate_path["post"]["operationId"] == "validateAgentDraft"
    val_responses = validate_path["post"]["responses"]
    for expected_code in ("200", "401", "403", "404", "422", "503"):
        assert expected_code in val_responses, f"Missing {expected_code} in validate responses"

    # ValidateAgentDraftRequest
    validate_req_dto = ValidateAgentDraftDTO(spec=valid_spec)
    val_req_schema = spec["components"]["schemas"]["ValidateAgentDraftRequest"]
    resolved_val_req_schema = copy.deepcopy(val_req_schema)
    val_req_validator = Draft202012Validator(resolved_val_req_schema)
    assert val_req_validator.is_valid(validate_req_dto.model_dump())
    assert val_req_validator.is_valid({})  # Omission validates the stored draft.
    assert val_req_validator.is_valid({"spec": {"mode": []}})  # Raw object is diagnosed.
    assert not val_req_validator.is_valid({"spec": None})
    assert not val_req_validator.is_valid({"spec": []})
    assert ValidateAgentDraftDTO().model_fields_set == set()
    assert ValidateAgentDraftDTO(spec={}).model_fields_set == {"spec"}

    # AgentDiagnostic & AgentValidationResult
    diag_dto = AgentDiagnosticDTO(
        field="modelRef",
        code="dependency_unavailable",
        message="Model configuration not found in workspace",
    )
    diag_schema = spec["components"]["schemas"]["AgentDiagnostic"]
    diag_val = Draft202012Validator(diag_schema)
    assert diag_val.is_valid(diag_dto.model_dump())

    val_result_dto = AgentValidationResultDTO(
        agent_id="00000000-0000-0000-0000-000000000001",
        revision=1,
        ready=False,
        diagnostics=[diag_dto],
    )
    res_schema = copy.deepcopy(spec["components"]["schemas"]["AgentValidationResult"])
    res_schema["properties"]["diagnostics"]["items"] = diag_schema
    res_val = Draft202012Validator(res_schema)
    assert res_val.is_valid(val_result_dto.model_dump())

    # Step 04-C: Publish endpoint and DTOs
    publish_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/publish"]
    assert "post" in publish_path
    assert publish_path["post"]["operationId"] == "publishAgentVersion"

    # Idempotency-Key header parameter
    params = publish_path["post"]["parameters"]
    idemp_param = next((p for p in params if p.get("name") == "Idempotency-Key"), None)
    assert idemp_param is not None
    assert idemp_param["in"] == "header"
    assert idemp_param["required"] is True

    # PublishAgentRequest
    pub_req_schema = spec["components"]["schemas"]["PublishAgentRequest"]
    pub_req_val = Draft202012Validator(pub_req_schema)
    pub_req_dto = PublishAgentRequestDTO(expected_revision=1)
    assert pub_req_val.is_valid(pub_req_dto.model_dump())
    assert pub_req_val.is_valid({"expected_revision": 2_147_483_647})
    assert not pub_req_val.is_valid({"expected_revision": 0})
    assert not pub_req_val.is_valid({"expected_revision": 2_147_483_648})
    assert not pub_req_val.is_valid({"expected_revision": False})
    assert not pub_req_val.is_valid({"expected_revision": "1"})
    assert not pub_req_val.is_valid({})

    # PublishAgentResponse
    pub_res_schema = spec["components"]["schemas"]["PublishAgentResponse"]
    pub_res_val = Draft202012Validator(pub_res_schema)
    pub_res_dto = PublishResponseDTO(
        agent_id="00000000-0000-0000-0000-000000000001",
        agent_version_id="00000000-0000-0000-0000-000000000002",
        version=1,
        bundle_sha256="a" * 64,
        is_immutable=True,
        created_at="2026-11-16T12:00:00Z",
    )
    assert pub_res_val.is_valid(pub_res_dto.model_dump())

    pub_responses = publish_path["post"]["responses"]
    for expected_code in ("200", "400", "401", "403", "404", "409", "412", "422", "503"):
        assert expected_code in pub_responses, f"Missing {expected_code} in publish responses"


def test_agent_promotions_and_versions_openapi_contract() -> None:
    """Verify Step 04-D OpenAPI 3.1 endpoints, DTOs and JSON Schemas for versions, publications, and promotions."""
    root = Path(__file__).resolve().parents[2]
    spec_path = root / "contracts" / "openapi" / "control-api.openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))

    # 1. Versions endpoint
    ver_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/versions"]
    assert "get" in ver_path
    assert ver_path["get"]["operationId"] == "listAgentVersions"
    for expected_code in ("200", "400", "401", "403", "404", "422", "503"):
        assert expected_code in ver_path["get"]["responses"]

    # 2. Publications endpoint
    pub_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/publications"]
    assert "get" in pub_path
    assert pub_path["get"]["operationId"] == "listAgentPublications"
    for expected_code in ("200", "401", "403", "404", "503"):
        assert expected_code in pub_path["get"]["responses"]

    # 3. Promotions endpoint
    promo_path = spec["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/promotions"]
    assert "post" in promo_path
    assert promo_path["post"]["operationId"] == "promoteAgentVersion"
    for expected_code in ("200", "400", "401", "403", "404", "409", "412", "422", "503"):
        assert expected_code in promo_path["post"]["responses"]

    # 4. JSON Schema validation
    schemas = spec["components"]["schemas"]
    req_schema = schemas["PromoteAgentVersionRequest"]
    res_schema = schemas["PromoteAgentVersionResponse"]
    ver_schema = schemas["AgentVersionMetadata"]
    pub_schema = schemas["AgentPublication"]

    req_validator = Draft202012Validator(req_schema)
    res_validator = Draft202012Validator(res_schema)
    ver_validator = Draft202012Validator(ver_schema)
    pub_validator = Draft202012Validator(pub_schema)

    assert req_validator.is_valid({
        "agent_version_id": "00000000-0000-0000-0000-000000000001",
        "environment": "production",
        "expected_publication_revision": 0,
    })
    assert req_validator.is_valid({
        "agent_version_id": "00000000-0000-0000-0000-000000000001",
        "environment": "production",
        "expected_publication_revision": 2_147_483_647,
    })
    assert not req_validator.is_valid({
        "agent_version_id": "00000000-0000-0000-0000-000000000001",
        "environment": "production",
        "expected_publication_revision": 2_147_483_648,
    })
    assert not req_validator.is_valid({
        "agent_version_id": "00000000-0000-0000-0000-000000000001",
        "environment": "production",
        "expected_publication_revision": False,
    })
    assert not req_validator.is_valid({
        "agent_version_id": "00000000-0000-0000-0000-000000000001",
        "environment": "production",
        "expected_publication_revision": "0",
    })
    assert not req_validator.is_valid({"expected_publication_revision": -1})

    assert res_validator.is_valid({
        "publication_id": "00000000-0000-0000-0000-000000000001",
        "agent_id": "00000000-0000-0000-0000-000000000002",
        "agent_version_id": "00000000-0000-0000-0000-000000000003",
        "version": 1,
        "environment": "production",
        "revision": 1,
        "bundle_sha256": "a" * 64,
        "published_at": "2026-11-17T12:00:00Z",
        "is_noop": False,
    })

    assert ver_validator.is_valid({
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "agent_version_id": "00000000-0000-0000-0000-000000000002",
        "version": 1,
        "bundle_sha256": "b" * 64,
        "is_immutable": True,
        "created_at": "2026-11-17T12:00:00Z",
        "published_by": "00000000-0000-0000-0000-000000000003",
        "model_name": "Claude 3.5 Sonnet",
    })

    assert pub_validator.is_valid({
        "publication_id": "00000000-0000-0000-0000-000000000001",
        "agent_id": "00000000-0000-0000-0000-000000000002",
        "agent_version_id": "00000000-0000-0000-0000-000000000003",
        "version": 1,
        "environment": "development",
        "revision": 2,
        "bundle_sha256": "c" * 64,
        "published_at": "2026-11-17T12:00:00Z",
    })
