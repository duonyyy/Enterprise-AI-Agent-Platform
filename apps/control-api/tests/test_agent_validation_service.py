"""Acceptance unit and boundary tests for Step 04-B: Agent Dependency Validation & Readiness (B01..B09).

Authority:
- SPEC-AGENT-001 (Section 4 & 5): Validation endpoint and diagnostics.
- ADR-022, DEC-STEP04: R04-REF/R04-SNAPSHOT review gates (not a declaration of approval).
- TEST-AG-001: Acceptance Plan Matrix B01..B09.
"""

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
from control_api.modules.agents.api.dependencies import get_agent_validation_service
from control_api.modules.agents.models.errors import AgentDatabaseUnavailableError
from control_api.modules.agents.models.ports import (
    AgentDependencyResolverPort,
    AgentRepositoryPort,
    ResolvedKnowledgeBase,
    ResolvedModelConfig,
    ResolvedSkillVersion,
    ResolvedToolVersion,
)
from control_api.modules.agents.models.records import AgentDraftEntity
from control_api.modules.agents.services.agent_validation_service import AgentValidationService
from fastapi.testclient import TestClient
from platform_common.principal import AuthenticatedPrincipal


def _principal(
    role: str = "developer",
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=str(user_id or uuid4()),
        tenant_id=str(tenant_id or uuid4()),
        workspace_id=str(workspace_id or uuid4()),
        roles=[role],
        permissions=["agent:read", "agent:edit"],
    )


def _valid_spec(
    slug: str = "valid-agent",
    model_ref: str = "00000000-0000-0000-0000-000000000001",
    tool_refs: list[str] | None = None,
    skill_refs: list[dict[str, str]] | None = None,
    knowledge_refs: list[str] | None = None,
    workflow_ref: str | None = None,
    approval_policy_ref: str | None = None,
    mode: str = "fixed_plan",
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": slug, "displayName": "Valid Agent"},
        "mode": mode,
        "modelRef": model_ref,
        "persona": {"instructions": "You are a helpful assistant."},
        "toolRefs": tool_refs if tool_refs is not None else [],
        "skillRefs": skill_refs if skill_refs is not None else [],
        "knowledgeRefs": knowledge_refs if knowledge_refs is not None else [],
        "limits": {"maxSteps": 10, "maxWallTimeSeconds": 180, "maxTotalTokens": 16000},
    }
    if workflow_ref is not None:
        spec["workflowRef"] = workflow_ref
    if approval_policy_ref is not None:
        spec["approvalPolicyRef"] = approval_policy_ref
    return spec


def _make_draft(
    agent_id: UUID,
    slug: str,
    spec: dict[str, Any],
    revision: int = 1,
) -> AgentDraftEntity:
    return AgentDraftEntity(
        agent_id=agent_id,
        slug=slug,
        display_name="Valid Agent",
        revision=revision,
        archived_at=None,
        created_at=datetime.now(UTC),
        spec=spec,
    )


# --- B01: Supported valid refs in scope -> ready=True ---
def test_b01_supported_valid_refs_ready_true() -> None:
    principal = _principal(role="developer")
    tenant_id = UUID(principal.tenant_id)
    workspace_id = UUID(principal.workspace_id)
    agent_id = uuid4()
    model_id = uuid4()
    tool_ver_id = uuid4()
    skill_id = uuid4()
    skill_ver_id = uuid4()
    kb_id = uuid4()

    spec = _valid_spec(
        slug="researcher",
        model_ref=str(model_id),
        tool_refs=[str(tool_ver_id)],
        skill_refs=[{"skillId": str(skill_id), "skillVersionId": str(skill_ver_id)}],
        knowledge_refs=[str(kb_id)],
    )
    draft = _make_draft(agent_id, "researcher", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="gpt-4o-config",
            provider="openai",
            provider_model_id="gpt-4o",
            status="active",
            settings={"temperature": 0.2},
        )
    )
    resolver.resolve_tool_version = AsyncMock(
        return_value=ResolvedToolVersion(
            id=tool_ver_id,
            tool_id=uuid4(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            tool_key="web-search",
            kind="builtin",
            version=1,
            risk="read",
            sha256="a" * 64,
            mcp_status=None,
            has_invoke_grant=True,
        )
    )
    resolver.resolve_skill_version = AsyncMock(
        return_value=ResolvedSkillVersion(
            skill_id=skill_id,
            skill_version_id=skill_ver_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_slug="summarizer",
            skill_status="active",
            version=1,
            bundle_sha256="b" * 64,
        )
    )
    resolver.resolve_knowledge_base = AsyncMock(
        return_value=ResolvedKnowledgeBase(
            id=kb_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            slug="engineering-handbook",
            name="Engineering Handbook",
            visibility="workspace",
            has_access=True,
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is True
    assert result.diagnostics == []
    assert result.revision == 1
    assert result.agent_id == agent_id


# --- B02: Cross-tenant / cross-workspace or missing ref -> dependency_unavailable without resource leak ---
def test_b02_cross_scope_or_missing_ref_is_generic_unavailable() -> None:
    principal = _principal()
    agent_id = uuid4()
    missing_model_id = uuid4()

    spec = _valid_spec(model_ref=str(missing_model_id))
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    # Resolver returns None (e.g. filtered out by RLS / cross-workspace)
    resolver.resolve_model_config = AsyncMock(return_value=None)

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.field == "modelRef"
    assert diag.code == "dependency_unavailable"
    # Must NOT leak foreign workspace or model details
    assert "not found" in diag.message.lower()


# --- B03: skillId and skillVersionId mismatch -> dependency_unavailable ---
def test_b03_skill_version_pair_mismatch() -> None:
    principal = _principal()
    tenant_id = UUID(principal.tenant_id)
    workspace_id = UUID(principal.workspace_id)
    agent_id = uuid4()
    model_id = uuid4()
    skill_a = uuid4()
    skill_b = uuid4()
    skill_ver_b = uuid4()

    # Spec pairs skill_a with skill_ver_b
    spec = _valid_spec(
        model_ref=str(model_id),
        skill_refs=[{"skillId": str(skill_a), "skillVersionId": str(skill_ver_b)}],
    )
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="gpt-4o",
            provider="openai",
            provider_model_id="gpt-4o",
            status="active",
            settings={},
        )
    )
    # skill_ver_b actually belongs to skill_b, NOT skill_a
    resolver.resolve_skill_version = AsyncMock(
        return_value=ResolvedSkillVersion(
            skill_id=skill_b,
            skill_version_id=skill_ver_b,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_slug="skill-b",
            skill_status="active",
            version=1,
            bundle_sha256="c" * 64,
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.field == "skillRefs[0]"
    assert diag.code == "dependency_unavailable"
    assert "does not belong" in diag.message


# --- B04: Disabled status, revoked MCP mapping, missing tool grant ---
def test_b04_disabled_status_and_denied_grants() -> None:
    principal = _principal(role="viewer")
    tenant_id = UUID(principal.tenant_id)
    workspace_id = UUID(principal.workspace_id)
    agent_id = uuid4()
    model_id = uuid4()
    tool_mcp_id = uuid4()
    tool_grant_denied_id = uuid4()
    skill_id = uuid4()
    skill_ver_id = uuid4()

    spec = _valid_spec(
        model_ref=str(model_id),
        tool_refs=[str(tool_mcp_id), str(tool_grant_denied_id)],
        skill_refs=[{"skillId": str(skill_id), "skillVersionId": str(skill_ver_id)}],
    )
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    # 1. Model is disabled
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            name="gpt-4o",
            provider="openai",
            provider_model_id="gpt-4o",
            status="disabled",
            settings={},
        )
    )

    # 2. Tool 1 is MCP with status revoked
    # 3. Tool 2 lacks invoke grant for caller role
    async def mock_resolve_tool(session, *, tenant_id, workspace_id, tool_version_id, caller_role):
        if tool_version_id == tool_mcp_id:
            return ResolvedToolVersion(
                id=tool_mcp_id,
                tool_id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                tool_key="jira-mcp",
                kind="mcp",
                version=1,
                risk="write",
                sha256="d" * 64,
                mcp_status="revoked",
                has_invoke_grant=True,
            )
        if tool_version_id == tool_grant_denied_id:
            return ResolvedToolVersion(
                id=tool_grant_denied_id,
                tool_id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                tool_key="db-query",
                kind="builtin",
                version=1,
                risk="write",
                sha256="e" * 64,
                mcp_status=None,
                has_invoke_grant=False,
            )
        return None

    resolver.resolve_tool_version = AsyncMock(side_effect=mock_resolve_tool)

    # 4. Skill is archived
    resolver.resolve_skill_version = AsyncMock(
        return_value=ResolvedSkillVersion(
            skill_id=skill_id,
            skill_version_id=skill_ver_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_slug="archived-skill",
            skill_status="archived",
            version=1,
            bundle_sha256="f" * 64,
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is False
    codes = [d.code for d in result.diagnostics]
    fields = [d.field for d in result.diagnostics]

    assert "modelRef" in fields
    assert "toolRefs[0]" in fields
    assert "toolRefs[1]" in fields
    assert "skillRefs[0]" in fields

    assert "dependency_unavailable" in codes
    assert "dependency_not_permitted" in codes


# --- B05: Unsupported mode/workflow/approval -> unsupported_capability ---
def test_b05_unsupported_mode_workflow_and_approval() -> None:
    principal = _principal()
    agent_id = uuid4()
    model_id = uuid4()

    spec = _valid_spec(
        model_ref=str(model_id),
        mode="react",
        workflow_ref=str(uuid4()),
        approval_policy_ref=str(uuid4()),
    )
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="gpt-4o",
            provider="openai",
            provider_model_id="gpt-4o",
            status="active",
            settings={},
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is False
    unsupported = [d for d in result.diagnostics if d.code == "unsupported_capability"]
    unsupported_fields = {d.field for d in unsupported}
    assert "mode" in unsupported_fields
    assert "workflowRef" in unsupported_fields
    assert "approvalPolicyRef" in unsupported_fields


# --- B06: Empty optional refs -> ready=True when modelRef is valid ---
def test_b06_empty_optional_refs_ready_true() -> None:
    principal = _principal()
    agent_id = uuid4()
    model_id = uuid4()

    spec = _valid_spec(
        model_ref=str(model_id),
        tool_refs=[],
        skill_refs=[],
        knowledge_refs=[],
        mode="fixed_plan",
    )
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="local-qwen",
            provider="ollama",
            provider_model_id="qwen2.5:7b",
            status="active",
            settings={},
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is True
    assert result.diagnostics == []


# --- B07: Model config settings provenance captured, not credential ---
def test_b07_model_config_settings_provenance() -> None:
    principal = _principal()
    agent_id = uuid4()
    model_id = uuid4()

    spec = _valid_spec(model_ref=str(model_id))
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="claude-3-5-sonnet",
            provider="anthropic",
            provider_model_id="claude-3-5-sonnet-20241022",
            status="active",
            settings={"max_tokens": 8192, "temperature": 0.0},
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is True
    call = resolver.resolve_model_config.call_args
    assert call.kwargs["model_id"] == model_id
    assert call.kwargs["workspace_id"] == UUID(principal.workspace_id)


# --- B08: Knowledge base bind vs end-user read ACL ---
def test_b08_restricted_knowledge_base_denied_without_grant() -> None:
    principal = _principal(role="developer")
    agent_id = uuid4()
    model_id = uuid4()
    kb_id = uuid4()

    spec = _valid_spec(model_ref=str(model_id), knowledge_refs=[str(kb_id)])
    draft = _make_draft(agent_id, "valid-agent", spec)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="gpt-4o",
            provider="openai",
            provider_model_id="gpt-4o",
            status="active",
            settings={},
        )
    )
    # Restricted base where caller has_access is False
    resolver.resolve_knowledge_base = AsyncMock(
        return_value=ResolvedKnowledgeBase(
            id=kb_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            slug="restricted-hr-docs",
            name="HR Docs",
            visibility="restricted",
            has_access=False,
        )
    )

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id)
    )

    assert result.ready is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.field == "knowledgeRefs[0]"
    assert diag.code == "dependency_not_permitted"


# --- B09: HTTP Route integration and validation does not mutate revision or state ---
def test_b09_http_route_validation_and_no_mutation() -> None:
    principal = _principal(role="developer")
    workspace_id = principal.workspace_id
    agent_id = uuid4()
    model_id = uuid4()

    spec = _valid_spec(model_ref=str(model_id))
    draft = _make_draft(agent_id, "valid-agent", spec, revision=3)

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)
    repo.replace_draft_spec = AsyncMock()
    repo.create_draft = AsyncMock()

    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="gpt-4o",
            provider="openai",
            provider_model_id="gpt-4o",
            status="active",
            settings={},
        )
    )

    validation_service = AgentValidationService(repository=repo, resolver=resolver)
    session = MagicMock()

    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_agent_validation_service] = lambda: validation_service

    try:
        client = TestClient(app)

        # 1. Successful validation of persisted draft
        resp = client.post(f"/v1/workspaces/{workspace_id}/agents/{agent_id}/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == str(agent_id)
        assert data["revision"] == 3
        assert data["ready"] is True
        assert data["diagnostics"] == []

        # Assert no writes occurred on repository
        repo.replace_draft_spec.assert_not_awaited()
        repo.create_draft.assert_not_awaited()

        # 2. Pre-validation with a modified spec payload in body
        invalid_mode_spec = _valid_spec(model_ref=str(model_id), mode="react")
        resp_preval = client.post(
            f"/v1/workspaces/{workspace_id}/agents/{agent_id}/validate",
            json={"spec": invalid_mode_spec},
        )
        assert resp_preval.status_code == 200
        preval_data = resp_preval.json()
        assert preval_data["ready"] is False
        assert any(d["code"] == "unsupported_capability" for d in preval_data["diagnostics"])

        # 3. Non-existent agent in workspace returns 404
        repo.get_draft = AsyncMock(return_value=None)
        resp_404 = client.post(f"/v1/workspaces/{workspace_id}/agents/{uuid4()}/validate")
        assert resp_404.status_code == 404

        # 4. Actor without agent:edit returns 403
        viewer_principal = _principal(role="viewer")
        viewer_principal.permissions = ["agent:read"]  # Missing agent:edit
        app.dependency_overrides[get_current_principal] = lambda: viewer_principal
        resp_403 = client.post(f"/v1/workspaces/{workspace_id}/agents/{agent_id}/validate")
        assert resp_403.status_code == 403

    finally:
        app.dependency_overrides.clear()


# --- B10: Raw schema errors, slug mutation rejection, and malformed ref inputs ---
def test_b10_schema_errors_and_malformed_inputs() -> None:
    principal = _principal()
    agent_id = uuid4()

    # 1. Spec with altered slug and schema errors (maxSteps = 0)
    bad_spec = _valid_spec(slug="original-slug")
    bad_spec["metadata"]["name"] = "different-slug"
    bad_spec["limits"]["maxSteps"] = 0
    # Also add malformed refs: non-UUID modelRef, non-string toolRef, non-dict skillRef, non-UUID knowledgeRef
    bad_spec["modelRef"] = "not-a-uuid"
    bad_spec["toolRefs"] = [123, "not-a-uuid"]  # type: ignore[list-item]
    bad_spec["skillRefs"] = ["not-a-dict", {"skillId": "invalid", "skillVersionId": "invalid"}]  # type: ignore[list-item]
    bad_spec["knowledgeRefs"] = [456, "not-a-uuid"]  # type: ignore[list-item]

    draft = _make_draft(agent_id, "original-slug", _valid_spec(slug="original-slug"))

    repo = MagicMock(spec=AgentRepositoryPort)
    repo.get_draft = AsyncMock(return_value=draft)

    resolver = MagicMock(spec=AgentDependencyResolverPort)

    service = AgentValidationService(repository=repo, resolver=resolver)
    result = asyncio.run(
        service.validate_draft(
            MagicMock(), principal=principal, agent_id=agent_id, spec_override=bad_spec
        )
    )

    assert result.ready is False
    fields = [d.field for d in result.diagnostics]
    codes = [d.code for d in result.diagnostics]

    # Schema errors
    assert "spec" in fields
    assert "invalid_spec" in codes
    # Invalid raw schema is returned before semantic dependency checks.
    resolver.resolve_model_config.assert_not_called()
    resolver.resolve_tool_version.assert_not_called()
    resolver.resolve_skill_version.assert_not_called()
    resolver.resolve_knowledge_base.assert_not_called()


# Regression cases use mocked storage boundaries, not live PostgreSQL evidence.


def _validation_fixture():
    principal = _principal()
    model_id, agent_id = uuid4(), uuid4()
    spec = _valid_spec(model_ref=str(model_id))
    repository = MagicMock(spec=AgentRepositoryPort)
    repository.get_draft = AsyncMock(return_value=_make_draft(agent_id, "valid-agent", spec))
    resolver = MagicMock(spec=AgentDependencyResolverPort)
    resolver.resolve_model_config = AsyncMock(
        return_value=ResolvedModelConfig(
            id=model_id,
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            name="fixture-model",
            provider="fixture",
            provider_model_id="unprovisioned-fixture",
            status="active",
            settings={},
        )
    )
    return (
        principal,
        agent_id,
        spec,
        repository,
        resolver,
        AgentValidationService(repository, resolver),
    )


@pytest.mark.parametrize("mode", [["fixed_plan"], {"bad": "type"}, 123, None])
def test_invalid_mode_is_diagnosed_without_resolver_queries(mode: Any) -> None:
    principal, agent_id, spec, _, resolver, service = _validation_fixture()
    spec["mode"] = mode
    resolver.resolve_model_config.side_effect = AgentDatabaseUnavailableError()
    result = asyncio.run(
        service.validate_draft(
            MagicMock(),
            principal=principal,
            agent_id=agent_id,
            spec_override=spec,
        )
    )
    assert not result.ready
    assert all(d.code == "invalid_spec" for d in result.diagnostics)
    resolver.resolve_model_config.assert_not_awaited()
    resolver.resolve_tool_version.assert_not_called()
    resolver.resolve_skill_version.assert_not_called()
    resolver.resolve_knowledge_base.assert_not_called()


def test_invalid_limits_return_diagnostics_when_dependency_backend_is_down() -> None:
    principal, agent_id, spec, _, resolver, service = _validation_fixture()
    spec["limits"]["maxSteps"] = 0
    resolver.resolve_model_config.side_effect = AgentDatabaseUnavailableError()
    result = asyncio.run(
        service.validate_draft(
            MagicMock(),
            principal=principal,
            agent_id=agent_id,
            spec_override=spec,
        )
    )
    assert not result.ready
    assert any(d.code == "invalid_spec" for d in result.diagnostics)
    resolver.resolve_model_config.assert_not_awaited()


@pytest.mark.parametrize(
    "payload, status", [({"spec": None}, 422), ({"spec": []}, 422), (None, 422), ({}, 200)]
)
def test_http_spec_omission_and_null_follow_the_request_contract(payload: Any, status: int) -> None:
    principal, agent_id, _, _, resolver, service = _validation_fixture()
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    app.dependency_overrides[get_agent_validation_service] = lambda: service
    try:
        with TestClient(app) as client:
            url = f"/v1/workspaces/{principal.workspace_id}/agents/{agent_id}/validate"
            response = (
                client.post(url, content="null", headers={"Content-Type": "application/json"})
                if payload is None
                else client.post(url, json=payload)
            )
        assert response.status_code == status
        if status == 422:
            resolver.resolve_model_config.assert_not_awaited()
        else:
            assert response.json()["ready"] is True
            resolver.resolve_model_config.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


@pytest.mark.parametrize("hashes_equal", [False, True])
def test_mcp_readiness_is_unsupported_until_owner_resolver_is_approved(hashes_equal: bool) -> None:
    principal, agent_id, spec, _, resolver, service = _validation_fixture()
    tool_id = uuid4()
    spec["toolRefs"] = [str(tool_id)]
    resolver.resolve_tool_version = AsyncMock(
        return_value=ResolvedToolVersion(
            id=tool_id,
            tool_id=uuid4(),
            tenant_id=UUID(principal.tenant_id),
            workspace_id=UUID(principal.workspace_id),
            tool_key="fixture-mcp",
            kind="mcp",
            version=1,
            risk="read",
            sha256="a" * 64,
            mcp_status="approved",
            has_invoke_grant=True,
            connection_status="active",
            approved_schema_sha256=("a" if hashes_equal else "b") * 64,
        )
    )
    result = asyncio.run(
        service.validate_draft(
            MagicMock(),
            principal=principal,
            agent_id=agent_id,
            spec_override=spec,
        )
    )
    assert not result.ready
    assert any(
        d.field == "toolRefs[0]" and d.code == "unsupported_capability" for d in result.diagnostics
    )
    assert not any("drift" in d.message.lower() for d in result.diagnostics)


def test_rf04_02_duplicate_and_conflicting_dependency_refs_are_diagnosed() -> None:
    """RF04-02: Duplicate tool/KB/skill refs and two versions of one skill must fail readiness with invalid_spec."""
    principal, agent_id, spec, _, resolver, service = _validation_fixture()
    tenant_id = UUID(principal.tenant_id)
    workspace_id = UUID(principal.workspace_id)

    tool_v1 = uuid4()
    tool_v2 = uuid4()
    shared_tool_id = uuid4()
    skill_id = uuid4()
    skill_v1 = uuid4()
    skill_v2 = uuid4()
    kb_id = uuid4()

    resolver.resolve_tool_version = AsyncMock(
        side_effect=lambda _s, *, tool_version_id, **_: ResolvedToolVersion(
            id=tool_version_id,
            tool_id=shared_tool_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            tool_key="calc",
            kind="builtin",
            version=1 if tool_version_id == tool_v1 else 2,
            risk="read",
            sha256="a" * 64,
            mcp_status=None,
            has_invoke_grant=True,
        )
    )
    resolver.resolve_skill_version = AsyncMock(
        side_effect=lambda _s, *, skill_id, skill_version_id, **_: ResolvedSkillVersion(
            skill_id=skill_id,
            skill_version_id=skill_version_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            skill_slug="my-skill",
            skill_status="active",
            version=1 if skill_version_id == skill_v1 else 2,
            bundle_sha256="b" * 64,
        )
    )
    resolver.resolve_knowledge_base = AsyncMock(
        return_value=ResolvedKnowledgeBase(
            id=kb_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            slug="kb-main",
            name="KB Main",
            visibility="workspace",
            has_access=True,
        )
    )

    # 1. Duplicate toolRefs (uppercase vs lowercase normalized UUID)
    spec_dup_tool = {**spec, "toolRefs": [str(tool_v1).lower(), str(tool_v1).upper()]}
    res_tool = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=spec_dup_tool)
    )
    assert res_tool.ready is False
    assert any(d.field == "toolRefs[1]" and d.code == "invalid_spec" for d in res_tool.diagnostics)

    # 2. Two DIFFERENT toolVersionIds of the same parent tool ARE allowed by schema UNIQUE (..., tool_version_id)
    spec_two_tool_versions = {**spec, "toolRefs": [str(tool_v1), str(tool_v2)]}
    res_two_tv = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=spec_two_tool_versions)
    )
    assert res_two_tv.ready is True, f"Expected ready=True, got {res_two_tv.diagnostics}"

    # 3. Duplicate skill pair
    spec_dup_skill = {
        **spec,
        "skillRefs": [
            {"skillId": str(skill_id), "skillVersionId": str(skill_v1)},
            {"skillId": str(skill_id).upper(), "skillVersionId": str(skill_v1).upper()},
        ],
    }
    res_dup_skill = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=spec_dup_skill)
    )
    assert res_dup_skill.ready is False
    assert any(d.field == "skillRefs[1]" and d.code == "invalid_spec" for d in res_dup_skill.diagnostics)

    # 4. Two different versions of the same skill conflict with UNIQUE (..., skill_id)
    spec_two_skill_ver = {
        **spec,
        "skillRefs": [
            {"skillId": str(skill_id), "skillVersionId": str(skill_v1)},
            {"skillId": str(skill_id), "skillVersionId": str(skill_v2)},
        ],
    }
    res_two_sv = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=spec_two_skill_ver)
    )
    assert res_two_sv.ready is False
    assert any(d.field == "skillRefs[1]" and d.code == "invalid_spec" for d in res_two_sv.diagnostics)

    # 5. Duplicate knowledgeRefs (case-normalized UUID)
    spec_dup_kb = {**spec, "knowledgeRefs": [str(kb_id).lower(), str(kb_id).upper()]}
    res_kb = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=spec_dup_kb)
    )
    assert res_kb.ready is False
    assert any(d.field == "knowledgeRefs[1]" and d.code == "invalid_spec" for d in res_kb.diagnostics)


def test_rf04_05_uncanonicalizable_spec_reports_ready_false_without_500() -> None:
    """RF04-05: Numbers outside RFC 8785 exact integer range must produce invalid_spec diagnostic."""
    principal, agent_id, spec, _, _, service = _validation_fixture()
    large_spec = {
        **spec,
        "limits": {**spec["limits"], "maxTotalTokens": 2**53},
    }
    res = asyncio.run(
        service.validate_draft(MagicMock(), principal=principal, agent_id=agent_id, spec_override=large_spec)
    )
    assert res.ready is False
    assert any(d.field == "spec" and d.code == "invalid_spec" for d in res.diagnostics)
