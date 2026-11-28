"""Contract tests for 06-B runtime run admission DTOs, OpenAPI, and event schema boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from agent_runtime.application.run_admission import (
    RunMetadataResponseDTO,
    StartRunRequestDTO,
    StartRunResponseDTO,
    compute_run_admission_sha256,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_openapi_preserves_canonical_routes_and_idempotency_bounds() -> None:
    spec = yaml.safe_load(
        (ROOT / "contracts" / "openapi" / "runtime-api.openapi.yaml").read_text(encoding="utf-8")
    )
    post_runs = spec["paths"]["/v1/runs"]["post"]
    idemp = next(p for p in post_runs["parameters"] if p["name"] == "Idempotency-Key")
    assert idemp["in"] == "header"
    assert idemp["required"] is True
    assert idemp["schema"]["minLength"] == 1
    assert idemp["schema"]["maxLength"] == 200
    assert "202" in post_runs["responses"]
    assert "/v1/runs/{run_id}" in spec["paths"]
    assert "/v1/runs/{run_id}/cancel" in spec["paths"]
    assert "/internal/v1/runs/{run_id}/resume" in spec["paths"]
    assert spec["info"]["version"] == "2.3.0"
    cancel_op = spec["paths"]["/v1/runs/{run_id}/cancel"]["post"]
    assert "200" in cancel_op["responses"]
    assert "409" in cancel_op["responses"]


def test_admission_dtos_enforce_strict_schema_boundaries() -> None:
    w_id = uuid4()
    av_id = uuid4()
    req = StartRunRequestDTO(
        workspace_id=w_id,
        agent_version_id=av_id,
        inputs={"prompt": "Xin chào"},
    )
    assert req.workspace_id == w_id
    with pytest.raises(ValidationError):
        StartRunRequestDTO.model_validate(
            {
                "workspace_id": str(w_id),
                "agent_version_id": str(av_id),
                "inputs": {},
                "status": "succeeded",
            }
        )

    resp = StartRunResponseDTO(
        run_id=str(uuid4()),
        agent_version_id=str(av_id),
        conversation_id=None,
        status="queued",
        replayed=False,
        execution_available=False,
        availability_reason="no_executor_configured",
    )
    assert resp.execution_available is False

    meta = RunMetadataResponseDTO(
        run_id=resp.run_id,
        agent_version_id=str(av_id),
        conversation_id=None,
        status="queued",
        trace_id="trace-1",
        next_event_seq=2,
        started_at=None,
        finished_at=None,
        created_at="2026-11-27T00:00:00Z",
        queue_expires_at="2026-11-28T00:00:00Z",
    )
    assert "inputs" not in meta.model_dump()
    assert meta.queue_expires_at == "2026-11-28T00:00:00Z"


def test_cancel_dtos_enforce_strict_schema_boundaries() -> None:
    from agent_runtime.application.run_lifecycle import (
        CancelRunRequestDTO,
        CancelRunResponseDTO,
    )

    req = CancelRunRequestDTO()
    assert req.model_dump() == {}
    with pytest.raises(ValidationError):
        CancelRunRequestDTO.model_validate({"extra": "not_allowed"})

    resp = CancelRunResponseDTO(
        run_id=str(uuid4()),
        status="cancelled",
        changed=True,
        disposition="cancelled",
    )
    assert resp.changed is True
    assert resp.disposition == "cancelled"

    with pytest.raises(ValidationError):
        CancelRunResponseDTO.model_validate(
            {
                "run_id": str(uuid4()),
                "status": "cancelled",
                "changed": True,
                "disposition": "invalid_disposition",
            }
        )


def test_queued_event_uses_forward_schema_without_rewriting_v1() -> None:
    """New queued admission validates v1.1; historical v1.0 remains unchanged."""
    schema = json.loads(
        (ROOT / "contracts" / "jsonschema" / "run-event.v1.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    allowed_types = set(schema["properties"]["type"]["enum"])
    assert "run.started" in allowed_types
    assert "run.created" not in allowed_types
    assert "run.queued" not in allowed_types

    base_evt = {
        "schemaVersion": "1.0",
        "eventId": str(uuid4()),
        "runId": str(uuid4()),
        "workspaceId": str(uuid4()),
        "sequence": 1,
        "timestamp": "2026-11-27T00:00:00Z",
        "data": {"status": "queued"},
    }
    assert validator.is_valid({**base_evt, "type": "run.started"})
    assert not validator.is_valid({**base_evt, "type": "run.created"})
    assert not validator.is_valid({**base_evt, "type": "run.queued"})
    queued_schema = json.loads(
        (ROOT / "contracts/jsonschema/run-event.v1.1.schema.json").read_text(encoding="utf-8")
    )
    from agent_runtime.application.run_admission import (
        ADMISSION_EVENT_SCHEMA_VERSION,
        DEFAULT_ADMISSION_EVENT_TYPE,
    )

    current = Draft202012Validator(queued_schema)
    event = {
        **base_evt,
        "schemaVersion": ADMISSION_EVENT_SCHEMA_VERSION,
        "type": DEFAULT_ADMISSION_EVENT_TYPE,
    }
    current.validate(event)
    assert event["type"] == "run.queued"
    assert not current.is_valid({**event, "type": "run.created"})
    assert not current.is_valid({**event, "schemaVersion": "1.0"})


def test_admission_hash_binds_tenant_workspace_actor_and_version() -> None:
    t1, t2 = str(uuid4()), str(uuid4())
    w1, w2 = str(uuid4()), str(uuid4())
    u1, u2 = str(uuid4()), str(uuid4())
    av1, av2 = str(uuid4()), str(uuid4())
    base = compute_run_admission_sha256(
        tenant_id=t1,
        workspace_id=w1,
        actor_user_id=u1,
        agent_version_id=av1,
        conversation_id=None,
        inputs={"prompt": "test"},
    )
    assert base != compute_run_admission_sha256(
        tenant_id=t2,
        workspace_id=w1,
        actor_user_id=u1,
        agent_version_id=av1,
        conversation_id=None,
        inputs={"prompt": "test"},
    )
    assert base != compute_run_admission_sha256(
        tenant_id=t1,
        workspace_id=w2,
        actor_user_id=u1,
        agent_version_id=av1,
        conversation_id=None,
        inputs={"prompt": "test"},
    )
    assert base != compute_run_admission_sha256(
        tenant_id=t1,
        workspace_id=w1,
        actor_user_id=u2,
        agent_version_id=av1,
        conversation_id=None,
        inputs={"prompt": "test"},
    )
    assert base != compute_run_admission_sha256(
        tenant_id=t1,
        workspace_id=w1,
        actor_user_id=u1,
        agent_version_id=av2,
        conversation_id=None,
        inputs={"prompt": "test"},
    )
