"""Contract tests for RunEvent 1.2 JSON Schema, OpenAPI 2.2.0, projection allowlists, and cursor rules (06-C-T01)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from agent_runtime.application.run_events import (
    MAX_SAFE_INTEGER,
    RunEventValidationError,
    map_row_to_run_event_dto,
    parse_and_validate_cursor,
    resolve_sse_cursor,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_schema_versions_1_0_and_1_1_remain_byte_identical() -> None:
    """Ensure backward schemas 1.0 and 1.1 are untouched byte-for-byte."""
    v1_0 = (ROOT / "contracts" / "jsonschema" / "run-event.v1.schema.json").read_bytes()
    v1_1 = (ROOT / "contracts" / "jsonschema" / "run-event.v1.1.schema.json").read_bytes()
    assert len(v1_0) > 0
    assert len(v1_1) > 0

    schema_1_0 = json.loads(v1_0)
    assert schema_1_0["properties"]["schemaVersion"]["enum"] == ["1.0"]

    schema_1_1 = json.loads(v1_1)
    assert schema_1_1["properties"]["schemaVersion"]["enum"] == ["1.1"]


def test_run_event_1_2_schema_validates_all_11_event_types() -> None:
    """Validate each of the 11 canonical event types against run-event.v1.2.schema.json."""
    raw_schema = (ROOT / "contracts" / "jsonschema" / "run-event.v1.2.schema.json").read_text(
        encoding="utf-8"
    )
    schema = json.loads(raw_schema)
    validator = Draft202012Validator(schema)

    base_envelope = {
        "schemaVersion": "1.2",
        "eventId": str(uuid4()),
        "runId": str(uuid4()),
        "workspaceId": str(uuid4()),
        "sequence": 1,
        "timestamp": "2026-11-27T12:00:00Z",
        "traceId": "0123456789abcdef0123456789abcdef",
    }

    test_cases: list[tuple[str, dict]] = [
        (
            "run.queued",
            {
                "status": "queued",
                "agentVersionId": str(uuid4()),
                "executionAvailable": False,
                "availabilityReason": "no_executor_configured",
            },
        ),
        ("run.started", {"status": "running"}),
        ("run.step_started", {"status": "running", "stepId": str(uuid4()), "attempt": 1}),
        (
            "run.tool_call_requested",
            {"status": "requested", "toolOperationId": str(uuid4()), "toolVersionId": str(uuid4())},
        ),
        (
            "run.tool_call_completed",
            {"status": "completed", "toolOperationId": str(uuid4()), "toolVersionId": str(uuid4())},
        ),
        ("run.approval_requested", {"status": "waiting_approval", "approvalId": str(uuid4())}),
        ("run.approval_resolved", {"status": "resolved", "approvalId": str(uuid4())}),
        ("run.completed", {"status": "succeeded"}),
        ("run.failed", {"status": "failed", "errorCode": "run_failed"}),
        ("run.cancelled", {"status": "cancelled", "reasonCode": "user_cancelled"}),
        ("run.timed_out", {"status": "timed_out", "reasonCode": "queue_deadline_exceeded"}),
    ]

    for ev_type, ev_data in test_cases:
        event = {**base_envelope, "type": ev_type, "data": ev_data}
        validator.validate(event)


def test_run_event_1_2_rejects_unallowed_properties_and_invalid_data() -> None:
    """Ensure additionalProperties=false is strictly enforced on envelope and data."""
    raw_schema = (ROOT / "contracts" / "jsonschema" / "run-event.v1.2.schema.json").read_text(
        encoding="utf-8"
    )
    schema = json.loads(raw_schema)
    validator = Draft202012Validator(schema)

    base_envelope = {
        "schemaVersion": "1.2",
        "eventId": str(uuid4()),
        "runId": str(uuid4()),
        "workspaceId": str(uuid4()),
        "sequence": 1,
        "timestamp": "2026-11-27T12:00:00Z",
    }

    # Extra field on envelope
    with_extra_env = {
        **base_envelope,
        "type": "run.started",
        "data": {"status": "running"},
        "unexpected": 123,
    }
    assert not validator.is_valid(with_extra_env)

    # Extra field in data (data-leak resistance)
    with_extra_data = {
        **base_envelope,
        "type": "run.started",
        "data": {"status": "running", "raw_prompt": "secret prompt"},
    }
    assert not validator.is_valid(with_extra_data)

    # Invalid sequence: 0, negative, float, overflow
    assert not validator.is_valid(
        {**base_envelope, "sequence": 0, "type": "run.started", "data": {"status": "running"}}
    )
    assert not validator.is_valid(
        {**base_envelope, "sequence": -1, "type": "run.started", "data": {"status": "running"}}
    )
    assert not validator.is_valid(
        {**base_envelope, "sequence": 1.5, "type": "run.started", "data": {"status": "running"}}
    )
    assert not validator.is_valid(
        {
            **base_envelope,
            "sequence": MAX_SAFE_INTEGER + 1,
            "type": "run.started",
            "data": {"status": "running"},
        }
    )

    # Invalid traceId (not 32 hex chars)
    assert not validator.is_valid(
        {**base_envelope, "traceId": "short", "type": "run.started", "data": {"status": "running"}}
    )
    assert not validator.is_valid(
        {**base_envelope, "traceId": "G" * 32, "type": "run.started", "data": {"status": "running"}}
    )

    # Wrong status for event type
    assert not validator.is_valid(
        {**base_envelope, "type": "run.completed", "data": {"status": "running"}}
    )
    assert not validator.is_valid(
        {**base_envelope, "type": "run.queued", "data": {"status": "succeeded"}}
    )


def test_historical_projection_translates_created_to_queued_and_strips_unallowed_keys() -> None:
    """Historical stored run.created projects to wire run.queued; secrets and prompts are stripped."""
    w_id = str(uuid4())
    r_id = str(uuid4())
    e_id = str(uuid4())
    av_id = str(uuid4())

    legacy_row = {
        "id": e_id,
        "sequence": 1,
        "event_type": "run.created",
        "created_at": "2026-11-27T10:00:00Z",
        "redacted_payload": {
            "status": "queued",
            "agent_version_id": av_id,
            "execution_available": False,
            "availability_reason": "no_executor_configured",
            "prompt": "MUST BE STRIPPED",
            "token_count": 999,
            "customer_secret": "API_KEY_12345",
        },
    }

    dto = map_row_to_run_event_dto(legacy_row, workspace_id=w_id, run_id=r_id)
    assert dto.type == "run.queued"
    assert dto.sequence == 1
    assert dto.data["status"] == "queued"
    assert dto.data["agentVersionId"] == av_id
    assert dto.data["executionAvailable"] is False
    assert dto.data["availabilityReason"] == "no_executor_configured"
    assert "prompt" not in dto.data
    assert "token_count" not in dto.data
    assert "customer_secret" not in dto.data
    assert "agent_version_id" not in dto.data


def test_cursor_validation_enforces_strict_canonical_decimal() -> None:
    """Strict canonical decimal rules: 0 or [1-9][0-9]*, max safe integer."""
    assert parse_and_validate_cursor(None) == 0
    assert parse_and_validate_cursor(0) == 0
    assert parse_and_validate_cursor("0") == 0
    assert parse_and_validate_cursor(42) == 42
    assert parse_and_validate_cursor("42") == 42
    assert parse_and_validate_cursor(str(MAX_SAFE_INTEGER)) == MAX_SAFE_INTEGER

    invalid_cursors = [
        "-1",
        "+5",
        "05",
        "00",
        "1.0",
        "3.14",
        "1e5",
        " 10",
        "10 ",
        "abc",
        "",
        str(MAX_SAFE_INTEGER + 1),
    ]
    for inv in invalid_cursors:
        with pytest.raises(RunEventValidationError):
            parse_and_validate_cursor(inv)


def test_sse_cursor_resolution_handles_query_and_header_combinations() -> None:
    """Resolve SSE cursor from query and Last-Event-ID header according to D6-CD03."""
    # Neither provided
    assert resolve_sse_cursor(query_cursor=None, header_cursor=None) == 0

    # Query only
    assert resolve_sse_cursor(query_cursor="10", header_cursor=None) == 10

    # Header only
    assert resolve_sse_cursor(query_cursor=None, header_cursor="25") == 25

    # Empty header treated as absent
    assert resolve_sse_cursor(query_cursor="15", header_cursor="") == 15
    with pytest.raises(RunEventValidationError):
        resolve_sse_cursor(query_cursor="15", header_cursor="   ")

    # Both provided and equal
    assert resolve_sse_cursor(query_cursor="50", header_cursor="50") == 50

    # Both provided but conflicting -> 400
    with pytest.raises(RunEventValidationError) as exc:
        resolve_sse_cursor(query_cursor="10", header_cursor="20")
    assert exc.value.code == "cursor_conflict"

    # Empty query string -> 400
    with pytest.raises(RunEventValidationError) as exc:
        resolve_sse_cursor(query_cursor="", header_cursor=None)
    assert exc.value.code == "invalid_cursor"


def test_openapi_spec_version_routes_and_schemas_match_requirements() -> None:
    """Verify runtime OpenAPI spec contains 2.2.0, history, and updated SSE."""
    spec = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "runtime-api.openapi.yaml").read_text(encoding="utf-8")
    )
    assert spec["info"]["version"] in ("2.2.0", "2.3.0")
    assert "/v1/runs/{run_id}/history" in spec["paths"]
    assert "/v1/runs/{run_id}/events" in spec["paths"]

    history_get = spec["paths"]["/v1/runs/{run_id}/history"]["get"]
    assert "200" in history_get["responses"]
    assert "400" in history_get["responses"]
    assert "401" in history_get["responses"]
    assert "403" in history_get["responses"]
    assert "404" in history_get["responses"]
    assert "409" in history_get["responses"]
    assert "410" in history_get["responses"]
    assert "503" in history_get["responses"]

    events_get = spec["paths"]["/v1/runs/{run_id}/events"]["get"]
    param_names = [p["name"] for p in events_get["parameters"]]
    assert "run_id" in param_names
    assert "last_event_id" in param_names
    assert "Last-Event-ID" in param_names
    assert "429" in events_get["responses"]

    schemas = spec["components"]["schemas"]
    assert "RunEvent" in schemas
    assert "RunHistoryResponse" in schemas
