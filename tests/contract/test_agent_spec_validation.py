"""Contract verification for canonical AgentSpec JSON Schema validation.

Authority:
- ADR-004: Schema-first contracts at trust boundaries.
- SPEC-AGENT-001: agent-spec.v1.schema.json canonical specification.
- TP-STEP04 (D04-01): Validate raw JSON input against canonical schema before
  Pydantic coercion, defaults, or serialization.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from agent_contracts.agent_spec import AgentSpec
from agent_contracts.validation import (
    AgentSpecValidationError,
    load_canonical_agent_spec_schema,
    validate_agent_spec_raw,
)

BASE_SPEC: dict[str, Any] = {
    "schemaVersion": "1.0",
    "kind": "Agent",
    "metadata": {"name": "knowledge-assistant"},
    "mode": "fixed_plan",
    "modelRef": "unresolved-test-reference",
    "persona": {"instructions": "Answer only using authorized sources."},
    "limits": {"maxSteps": 8},
}

# 20 public regression test vectors mirroring the full canonical schema matrix
CANONICAL_SPEC_CASES: list[dict[str, Any]] = [
    {"id": "minimal_valid", "valid": True, "set": {}, "remove": []},
    {
        "id": "optional_refs_empty",
        "valid": True,
        "set": {"toolRefs": [], "knowledgeRefs": [], "skillRefs": []},
        "remove": [],
    },
    {
        "id": "exact_skill_pair_shape",
        "valid": True,
        "set": {"skillRefs": [{"skillId": "fixture-skill", "skillVersionId": "fixture-version"}]},
        "remove": [],
    },
    {
        "id": "nested_extra_current_schema_allows",
        "valid": True,
        "set": {
            "persona": {
                "instructions": "Fixture",
                "owner_extension": "allowed by current nested schema",
            }
        },
        "remove": [],
    },
    {"id": "missing_schema_version", "valid": False, "set": {}, "remove": ["schemaVersion"]},
    {"id": "missing_kind", "valid": False, "set": {}, "remove": ["kind"]},
    {"id": "missing_limits", "valid": False, "set": {}, "remove": ["limits"]},
    {
        "id": "invalid_name_pattern",
        "valid": False,
        "set": {"metadata": {"name": "Invalid Name"}},
        "remove": [],
    },
    {"id": "missing_instructions", "valid": False, "set": {"persona": {}}, "remove": []},
    {"id": "wrong_mode", "valid": False, "set": {"mode": "react_bounded"}, "remove": []},
    {"id": "workflow_explicit_null", "valid": False, "set": {"workflowRef": None}, "remove": []},
    {
        "id": "approval_policy_explicit_null",
        "valid": False,
        "set": {"approvalPolicyRef": None},
        "remove": [],
    },
    {
        "id": "metadata_display_name_null",
        "valid": False,
        "set": {"metadata": {"name": "knowledge-assistant", "displayName": None}},
        "remove": [],
    },
    {"id": "unexpected_root_field", "valid": False, "set": {"is_approved": True}, "remove": []},
    {"id": "max_steps_zero", "valid": False, "set": {"limits": {"maxSteps": 0}}, "remove": []},
    {
        "id": "max_steps_above_bound",
        "valid": False,
        "set": {"limits": {"maxSteps": 51}},
        "remove": [],
    },
    {
        "id": "max_steps_fractional",
        "valid": False,
        "set": {"limits": {"maxSteps": 1.5}},
        "remove": [],
    },
    {
        "id": "skill_string_invalid",
        "valid": False,
        "set": {"skillRefs": ["fixture-skill"]},
        "remove": [],
    },
    {
        "id": "skill_missing_version",
        "valid": False,
        "set": {"skillRefs": [{"skillId": "fixture-skill"}]},
        "remove": [],
    },
    {
        "id": "skill_extra_field",
        "valid": False,
        "set": {
            "skillRefs": [
                {"skillId": "fixture-skill", "skillVersionId": "fixture-version", "approved": True}
            ]
        },
        "remove": [],
    },
]


def _build_payload(case: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(BASE_SPEC)
    for k in case.get("remove", []):
        payload.pop(k, None)
    for k, v in case.get("set", {}).items():
        payload[k] = v
    return payload


def test_canonical_schema_loads_and_has_draft202012_meta():
    schema = load_canonical_agent_spec_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "AgentSpec"
    assert "schemaVersion" in schema["required"]


@pytest.mark.parametrize("case", CANONICAL_SPEC_CASES, ids=lambda c: c["id"])
def test_agent_spec_cases_matrix(case: dict[str, Any]):
    payload = _build_payload(case)
    is_valid = case["valid"]

    if is_valid:
        errors = validate_agent_spec_raw(payload)
        assert errors == []
        # Successfully parsed into Pydantic model after raw validation passes
        model = AgentSpec.model_validate(payload)
        assert model.metadata.name == payload["metadata"]["name"]
    else:
        with pytest.raises(AgentSpecValidationError) as exc_info:
            validate_agent_spec_raw(payload)
        assert len(exc_info.value.errors) > 0


def test_roundtrip_preserves_nested_extras_where_schema_allows():
    raw = copy.deepcopy(BASE_SPEC)
    raw["persona"]["owner_extension"] = "preserved"
    raw["metadata"]["custom_label"] = "custom_value"

    # 1. Raw validation passes
    assert validate_agent_spec_raw(raw) == []

    # 2. Pydantic model preserves nested extra fields
    model = AgentSpec.model_validate(raw)
    dumped = model.model_dump(mode="json", exclude_unset=True)

    assert dumped["persona"]["owner_extension"] == "preserved"
    assert dumped["metadata"]["custom_label"] == "custom_value"


@pytest.mark.parametrize("container", ["metadata", "persona", "limits"])
def test_roundtrip_preserves_schema_valid_null_extensions(container: str) -> None:
    raw = copy.deepcopy(BASE_SPEC)
    raw[container]["owner_extension"] = None
    raw["toolRefs"] = ["second-ref", "first-ref"]
    assert validate_agent_spec_raw(raw) == []

    dumped = AgentSpec.model_validate(raw).model_dump(mode="json", exclude_unset=True)

    assert dumped == raw
    assert validate_agent_spec_raw(dumped) == []


def test_non_dict_input_rejected():
    with pytest.raises(AgentSpecValidationError, match="AgentSpec must be a JSON object"):
        validate_agent_spec_raw(["not", "an", "object"])


def test_explicit_null_is_not_masked_by_pydantic():
    raw_with_null = copy.deepcopy(BASE_SPEC)
    raw_with_null["workflowRef"] = None

    # Raw validation rejects explicit null
    with pytest.raises(AgentSpecValidationError) as exc:
        validate_agent_spec_raw(raw_with_null)
    assert any("workflowRef" in err for err in exc.value.errors)
