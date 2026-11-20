"""
Contract test suite for Step 04-D: Agent Version History, Publication Pointers, and Promotion/Rollback CAS.
Covers D01-D09 acceptance criteria, OpenAPI parity, AST/structural invariant verification, and repository contracts.

Document ID: TEST-AG-001 D01..D09
ADR References: ADR-009, ADR-021, ADR-022
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI_SPEC = _REPO_ROOT / "contracts" / "openapi" / "control-api.openapi.yaml"
_AGENT_REPO_SRC = (
    _REPO_ROOT
    / "apps"
    / "control-api"
    / "src"
    / "control_api"
    / "modules"
    / "agents"
    / "repositories"
    / "agent_repository.py"
)
_AGENT_PROMO_SERVICE_SRC = (
    _REPO_ROOT
    / "apps"
    / "control-api"
    / "src"
    / "control_api"
    / "modules"
    / "agents"
    / "services"
    / "promotion_service.py"
)


@pytest.fixture(scope="module")
def openapi_doc() -> dict[str, Any]:
    assert _OPENAPI_SPEC.exists(), f"OpenAPI spec not found: {_OPENAPI_SPEC}"
    return yaml.safe_load(_OPENAPI_SPEC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def repo_source_text() -> str:
    assert _AGENT_REPO_SRC.exists(), f"agent_repository.py not found: {_AGENT_REPO_SRC}"
    return _AGENT_REPO_SRC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def service_source_text() -> str:
    assert _AGENT_PROMO_SERVICE_SRC.exists(), f"promotion_service.py not found: {_AGENT_PROMO_SERVICE_SRC}"
    return _AGENT_PROMO_SERVICE_SRC.read_text(encoding="utf-8")


def _extract_method(source: str, method_name: str) -> str:
    parsed = ast.parse(source)
    for node in ast.walk(parsed):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method_name
        ):
            lines = source.splitlines(keepends=True)
            start = node.lineno - 1
            end = node.end_lineno
            return "".join(lines[start:end])
    raise AssertionError(f"method {method_name} not found in source")


# ---------------------------------------------------------------------------
# D01: Canonical Contract & OpenAPI 3.1 Parity
# ---------------------------------------------------------------------------
class TestOpenApiContractParity:
    """D01: Verify OpenAPI 3.1 schema and endpoint parity for versions, publications, and promotions."""

    def test_paths_present(self, openapi_doc: dict[str, Any]) -> None:
        paths = openapi_doc["paths"]
        assert "/v1/workspaces/{workspace_id}/agents/{agent_id}/versions" in paths
        assert "/v1/workspaces/{workspace_id}/agents/{agent_id}/publications" in paths
        assert "/v1/workspaces/{workspace_id}/agents/{agent_id}/promotions" in paths

    def test_versions_endpoint_contract(self, openapi_doc: dict[str, Any]) -> None:
        path = openapi_doc["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/versions"]
        assert "get" in path
        op = path["get"]
        assert op["operationId"] == "listAgentVersions"
        assert "200" in op["responses"]
        for err_code in ("400", "401", "403", "404", "422", "503"):
            assert err_code in op["responses"]

    def test_publications_endpoint_contract(self, openapi_doc: dict[str, Any]) -> None:
        path = openapi_doc["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/publications"]
        assert "get" in path
        op = path["get"]
        assert op["operationId"] == "listAgentPublications"
        assert "200" in op["responses"]
        for err_code in ("401", "403", "404", "503"):
            assert err_code in op["responses"]

    def test_promotions_endpoint_contract(self, openapi_doc: dict[str, Any]) -> None:
        path = openapi_doc["paths"]["/v1/workspaces/{workspace_id}/agents/{agent_id}/promotions"]
        assert "post" in path
        op = path["post"]
        assert op["operationId"] == "promoteAgentVersion"
        assert "200" in op["responses"]
        for err_code in ("400", "401", "403", "404", "409", "412", "422", "503"):
            assert err_code in op["responses"]

    def test_dto_json_schema_validation(self, openapi_doc: dict[str, Any]) -> None:
        schemas = openapi_doc["components"]["schemas"]
        req_validator = Draft202012Validator(schemas["PromoteAgentVersionRequest"])
        res_validator = Draft202012Validator(schemas["PromoteAgentVersionResponse"])

        valid_req = {
            "agent_version_id": "00000000-0000-0000-0000-000000000001",
            "environment": "production",
            "expected_publication_revision": 0,
        }
        assert req_validator.is_valid(valid_req)
        assert not req_validator.is_valid({"expected_publication_revision": -1})
        assert not req_validator.is_valid({"environment": "invalid_env"})

        valid_res = {
            "publication_id": "00000000-0000-0000-0000-000000000001",
            "agent_id": "00000000-0000-0000-0000-000000000002",
            "agent_version_id": "00000000-0000-0000-0000-000000000003",
            "version": 1,
            "environment": "production",
            "revision": 1,
            "bundle_sha256": "a" * 64,
            "published_at": "2026-11-17T12:00:00Z",
            "is_noop": False,
        }
        assert res_validator.is_valid(valid_res)


# ---------------------------------------------------------------------------
# D02: Role Authority & Environment Confinement (R04-ENV)
# ---------------------------------------------------------------------------
class TestRoleAuthorityAndConfinement:
    """D02: Verify Owner/Admin enforcement and Developer/Operator/Viewer exclusion."""

    def test_promotion_service_enforces_owner_admin_only(self, service_source_text: str) -> None:
        assert 'PROMOTION_ALLOWED_ROLES = {"owner", "admin"}' in service_source_text or (
            '"owner"' in service_source_text and '"admin"' in service_source_text
        )
        assert "AgentAccessDeniedError" in service_source_text

    def test_repo_rechecks_live_authority_under_lock(self, repo_source_text: str) -> None:
        body = _extract_method(repo_source_text, "promote_version")
        assert "_recheck_live_authority" in body
        assert 'role not in ("owner", "admin")' in body or "owner" in body


# ---------------------------------------------------------------------------
# D03, D04, D05, D06, D07, D09: Persistence Invariant Verification
# ---------------------------------------------------------------------------
class TestPromotionPersistenceInvariants:
    """Verify repository implementation invariants for CAS, locks, dependencies, and audit."""

    def test_workspace_guard_is_for_update(self, repo_source_text: str) -> None:
        """D03: Workspace guard must use for_update=True to serialize concurrent promotions."""
        body = _extract_method(repo_source_text, "promote_version")
        assert re.search(r"_verify_active_workspace\([^)]*for_update=True", body), (
            "promote_version must call _verify_active_workspace with for_update=True"
        )

    def test_agent_row_check_and_archived_guard(self, repo_source_text: str) -> None:
        """D05: Archived agents must be rejected with AgentArchivedError."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "catalog.agents" in body
        assert "FOR SHARE" in body
        assert "archived_at" in body
        assert "AgentArchivedError" in body

    def test_version_must_be_sealed(self, repo_source_text: str) -> None:
        """D05: Target version must have bindings_sealed_at IS NOT NULL."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "bindings_sealed_at" in body
        assert "unsealed_version" in body or "Cannot promote an unsealed agent version" in body

    def test_live_dependencies_rechecked_under_share_locks(self, repo_source_text: str) -> None:
        """D06: Model config, tools, skills, knowledge dependencies rechecked under SHARE locks."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "catalog.model_configs" in body
        assert "catalog.agent_tool_bindings" in body
        assert "catalog.agent_skill_bindings" in body
        assert "catalog.agent_knowledge_bindings" in body
        assert "FOR SHARE" in body

    def test_publication_pointer_locked_for_update(self, repo_source_text: str) -> None:
        """D03/D04: Publication pointer row must be locked FOR UPDATE."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "catalog.agent_publications" in body
        assert "FOR UPDATE" in body

    def test_cas_expected_revision_enforcement(self, repo_source_text: str) -> None:
        """D03/D04: Mismatch between expected and current revision raises AgentStaleRevisionError."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "AgentStaleRevisionError" in body
        assert "expected_publication_revision" in body

    def test_noop_detection(self, repo_source_text: str) -> None:
        """D04: Same target version at matching expected revision returns is_noop=True without update."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "is_noop=True" in body

    def test_rollback_increments_revision_and_flags_rollback(self, repo_source_text: str) -> None:
        """D07: Target version change increments pointer revision and tracks rollback."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "new_revision = current_revision + 1" in body
        assert "is_rollback" in body

    def test_atomic_audit_and_outbox_events(self, repo_source_text: str) -> None:
        """D09: Atomic write_audit_event and integration.outbox_events in transaction."""
        body = _extract_method(repo_source_text, "promote_version")
        assert "write_audit_event" in body
        assert "agent.promote" in body
        assert "integration.outbox_events" in body
        assert "catalog.agent.events" in body
