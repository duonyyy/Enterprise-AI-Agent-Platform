"""Unit and HTTP boundary tests for 06-B run admission, idempotency, D6-03, and fail-closed resume."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from agent_runtime.api import health
from agent_runtime.application.run_admission import (
    AdmittedRunMetadata,
    ExistingRunAdmission,
    PinnedAgentVersionRecord,
    RunAdmissionAccessDeniedError,
    RunAdmissionConflictError,
    RunAdmissionQuotaExceededError,
    RunAdmissionUnavailableError,
    RunAdmissionValidationError,
    RunResumeDisabledError,
    StartRunRequestDTO,
    compute_run_admission_sha256,
    validate_idempotency_key,
)
from agent_runtime.application.run_admission_service import RunAdmissionService
from agent_runtime.application.run_data import ObjectStorePort, StoredObjectReceipt
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.application.run_events import RunEventDTO, format_iso8601_utc, project_event_data
from agent_runtime.auth import RuntimePrincipal, get_runtime_principal
from agent_runtime.main import app
from agent_runtime.persistence.run_data_repository import ConversationRecord
from agent_runtime.settings import RuntimeSettings
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError


class _FakeObjectStore(ObjectStorePort):
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_put: Exception | None = None

    async def put_immutable_object(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        expected_sha256: str,
        content_type: str = "application/json; charset=utf-8",
    ) -> StoredObjectReceipt:
        if self.fail_put is not None:
            raise self.fail_put
        self.objects[(bucket, key)] = data
        return StoredObjectReceipt(
            ref=f"s3://{bucket}/{key}",
            bucket=bucket,
            key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            content_type=content_type,
        )

    async def get_verified_object(
        self,
        *,
        bucket: str,
        key: str,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        _ = (expected_sha256, expected_size_bytes, max_bytes)
        return self.objects[(bucket, key)]

    async def head_object(self, *, bucket: str, key: str) -> dict[str, Any] | None:
        data = self.objects.get((bucket, key))
        return {"content_length": len(data)} if data is not None else None

    async def delete_object(self, *, bucket: str, key: str) -> bool:
        return self.objects.pop((bucket, key), None) is not None

    async def aclose(self) -> None:
        return None


class _FakeOutbox:
    def __init__(self) -> None:
        self.committed: list[dict[str, Any]] = []

    async def publish_in_transaction(
        self,
        session: Any,
        topic: str,
        key: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        event_id: str | None = None,
        set_rls: bool = False,
    ) -> str:
        eid = event_id or str(uuid4())
        session.tx_state["outbox"].append(
            {
                "id": eid,
                "topic": topic,
                "key": key,
                "payload": payload,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "set_rls": set_rls,
            }
        )
        return eid


class _FakeSession:
    def __init__(self) -> None:
        self.tx_state: dict[str, list[dict[str, Any]]] = {
            "runs": [],
            "jobs": [],
            "events": [],
            "audits": [],
            "outbox": [],
            "messages": [],
        }


class _InMemoryAdmissionState:
    def __init__(self) -> None:
        self.runs: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.jobs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.conversations: dict[str, ConversationRecord] = {}
        self.version_error: Exception | None = None
        self.role: str = "developer"
        self.active_count_override: int | None = None
        self.simulate_race_winner: ExistingRunAdmission | None = None

    def session_factory(self):
        state = self

        @asynccontextmanager
        async def _ctx(tenant_id: str, workspace_id: str, user_id: str | None = None):
            _ = (tenant_id, workspace_id, user_id)
            sess = _FakeSession()
            yield sess
            for run in sess.tx_state["runs"]:
                key = (run["tenant_id"], run["workspace_id"], run["external_request_key"])
                state.runs[key] = run
            state.jobs.extend(sess.tx_state["jobs"])
            state.events.extend(sess.tx_state["events"])
            state.audits.extend(sess.tx_state["audits"])
            state.outbox.extend(sess.tx_state["outbox"])
            state.messages.extend(sess.tx_state["messages"])

        return _ctx


class _FakeRunDataRepo:
    def __init__(self, state: _InMemoryAdmissionState) -> None:
        self._state = state

    async def get_conversation(
        self, session: Any, *, tenant_id: str, workspace_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        _ = (session, tenant_id, workspace_id)
        return self._state.conversations.get(conversation_id)

    async def append_message(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        role: str,
        body_ref: str | None = None,
        body_ciphertext: bytes | None = None,
        token_count: int | None = None,
        message_id: str | None = None,
    ) -> str:
        _ = (body_ciphertext, token_count)
        mid = message_id or str(uuid4())
        session.tx_state["messages"].append(
            {
                "id": mid,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "role": role,
                "body_ref": body_ref,
            }
        )
        return mid


class _FakeAdmissionRepo:
    def __init__(self, state: _InMemoryAdmissionState) -> None:
        self._state = state

    async def verify_active_workspace_membership(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        for_share: bool = False,
    ) -> str:
        _ = (session, tenant_id, workspace_id, user_id, for_share)
        return self._state.role

    async def acquire_workspace_admission_lock(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        _ = (session, tenant_id, workspace_id)

    async def lookup_existing_admission(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        external_request_key: str,
        for_update: bool = False,
    ) -> ExistingRunAdmission | None:
        _ = (session, for_update)
        if self._state.simulate_race_winner is not None and for_update:
            return self._state.simulate_race_winner
        rec = self._state.runs.get((tenant_id, workspace_id, external_request_key))
        if rec is None:
            return None
        req_hash = rec.get("request_sha256")
        return ExistingRunAdmission(
            run_id=rec["run_id"],
            agent_version_id=rec["agent_version_id"],
            conversation_id=rec["conversation_id"],
            initiated_by=rec["initiated_by"],
            status=rec["status"],
            request_sha256=req_hash,
        )

    async def validate_pinned_agent_version(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        actor_user_id: str,
        caller_role: str,
        for_share: bool = False,
    ) -> PinnedAgentVersionRecord:
        _ = (session, tenant_id, workspace_id, actor_user_id, caller_role, for_share)
        if self._state.version_error is not None:
            raise self._state.version_error
        return PinnedAgentVersionRecord(
            agent_id=str(uuid4()),
            agent_version_id=agent_version_id,
            version=1,
            archived_at=None,
            bindings_sealed_at=datetime.now(UTC),
            model_config_id=str(uuid4()),
            frozen_spec={"mode": "fixed_plan"},
            bundle_manifest={},
        )

    async def count_active_workspace_runs(
        self, session: Any, *, tenant_id: str, workspace_id: str
    ) -> int:
        _ = (session, tenant_id, workspace_id)
        if self._state.active_count_override is not None:
            return self._state.active_count_override
        return len(self._state.runs)

    async def insert_run_if_absent(
        self,
        session: Any,
        *,
        run_id: str,
        tenant_id: str,
        workspace_id: str,
        agent_version_id: str,
        conversation_id: str | None,
        initiated_by: str,
        external_request_key: str,
        trace_id: str | None,
        request_sha256: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        queue_ttl_seconds: int = 86400,
    ) -> bool:
        _ = queue_ttl_seconds
        if self._state.simulate_race_winner is not None:
            return False
        if (tenant_id, workspace_id, external_request_key) in self._state.runs:
            return False
        session.tx_state["runs"].append(
            {
                "run_id": run_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "agent_version_id": agent_version_id,
                "conversation_id": conversation_id,
                "initiated_by": initiated_by,
                "external_request_key": external_request_key,
                "status": "queued",
                "trace_id": trace_id,
                "request_sha256": request_sha256,
                "input_ref": input_ref,
                "input_sha256": input_sha256,
                "input_size_bytes": input_size_bytes,
                "created_at": datetime.now(UTC),
            }
        )
        return True

    async def insert_queued_run_job(
        self,
        session: Any,
        *,
        job_id: str,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        payload_ref: str,
    ) -> str:
        session.tx_state["jobs"].append(
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "payload_ref": payload_ref,
                "status": "queued",
            }
        )
        return job_id

    async def insert_initial_run_event(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        event_type: str,
        redacted_payload: dict[str, Any],
    ) -> RunEventDTO:
        eid = str(uuid4())
        session.tx_state["events"].append(
            {
                "id": eid,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "run_id": run_id,
                "sequence": 1,
                "event_type": event_type,
                "redacted_payload": redacted_payload,
            }
        )
        return RunEventDTO(
            eventId=eid,
            runId=run_id,
            workspaceId=workspace_id,
            sequence=1,
            type=event_type,
            timestamp=format_iso8601_utc(datetime.now(UTC)),
            data=project_event_data(event_type, redacted_payload),
        )

    async def insert_admission_audit_event(
        self,
        session: Any,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_subject: str,
        run_id: str,
        operation_key: str,
        request_sha256: str,
        agent_version_id: str,
        input_ref: str,
        input_sha256: str,
        input_size_bytes: int,
        execution_available: bool,
        trace_id: str | None,
    ) -> str:
        aid = str(uuid4())
        session.tx_state["audits"].append(
            {
                "id": aid,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "actor_subject": actor_subject,
                "resource_id": run_id,
                "operation_key": operation_key,
                "request_sha256": request_sha256,
                "agent_version_id": agent_version_id,
                "input_ref": input_ref,
                "input_sha256": input_sha256,
                "input_size_bytes": input_size_bytes,
                "execution_available": execution_available,
                "trace_id": trace_id,
            }
        )
        return aid

    async def get_run_metadata(
        self, session: Any, *, tenant_id: str, workspace_id: str, run_id: str
    ) -> AdmittedRunMetadata | None:
        _ = session
        for rec in self._state.runs.values():
            if (
                rec["tenant_id"] == tenant_id
                and rec["workspace_id"] == workspace_id
                and rec["run_id"] == run_id
            ):
                return AdmittedRunMetadata(
                    run_id=rec["run_id"],
                    agent_version_id=rec["agent_version_id"],
                    conversation_id=rec["conversation_id"],
                    workflow_version_id=None,
                    initiated_by=rec["initiated_by"],
                    status=rec["status"],
                    trace_id=rec["trace_id"],
                    next_event_seq=2,
                    started_at=None,
                    finished_at=None,
                    created_at=rec["created_at"],
                )
        return None


def _make_principal(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    scopes: frozenset[str] = frozenset({"run:start", "run:read"}),
) -> RuntimePrincipal:
    return RuntimePrincipal(
        subject="user-subject",
        user_id=user_id or str(uuid4()),
        tenant_id=tenant_id or str(uuid4()),
        workspace_id=workspace_id or str(uuid4()),
        roles=("developer",),
        scopes=scopes,
        session_id=str(uuid4()),
    )


def _build_service(
    state: _InMemoryAdmissionState,
    *,
    store: _FakeObjectStore | None = None,
    max_queued: int = 100,
) -> tuple[RunAdmissionService, _FakeObjectStore]:
    obj_store = store or _FakeObjectStore()
    data_repo = _FakeRunDataRepo(state)
    data_service = RunDataService(
        object_store=obj_store,
        bucket="test-bucket",
        repository=data_repo,  # type: ignore[arg-type]
    )
    svc = RunAdmissionService(
        session_factory=state.session_factory(),
        run_data_service=data_service,
        admission_repository=_FakeAdmissionRepo(state),
        outbox_publisher=_FakeOutbox(),
        run_data_repository=data_repo,  # type: ignore[arg-type]
        max_queued_per_workspace=max_queued,
        execution_available=False,
        availability_reason="no_executor_configured",
    )
    return svc, obj_store


def test_canonical_intent_hash_and_idempotency_key_bounds() -> None:
    t_id = str(uuid4())
    w_id = str(uuid4())
    u1 = str(uuid4())
    u2 = str(uuid4())
    av = str(uuid4())

    h1 = compute_run_admission_sha256(
        tenant_id=t_id,
        workspace_id=w_id,
        actor_user_id=u1,
        agent_version_id=av,
        conversation_id=None,
        inputs={"b": 2, "a": "Xin chào"},
    )
    h2 = compute_run_admission_sha256(
        tenant_id=t_id,
        workspace_id=w_id,
        actor_user_id=u1,
        agent_version_id=av,
        conversation_id=None,
        inputs={"a": "Xin chào", "b": 2},
    )
    h_other_actor = compute_run_admission_sha256(
        tenant_id=t_id,
        workspace_id=w_id,
        actor_user_id=u2,
        agent_version_id=av,
        conversation_id=None,
        inputs={"a": "Xin chào", "b": 2},
    )
    assert h1 == h2
    assert h1 != h_other_actor

    with pytest.raises(RunAdmissionValidationError):
        compute_run_admission_sha256(
            tenant_id=t_id,
            workspace_id=w_id,
            actor_user_id=u1,
            agent_version_id=av,
            conversation_id=None,
            inputs={"bad": float("nan")},
        )

    assert validate_idempotency_key("  key-123  ") == "key-123"
    for bad in (None, "", "   ", "a" * 201, "bad\nkey", "bad\rkey"):
        with pytest.raises(RunAdmissionValidationError):
            validate_idempotency_key(bad)


def test_start_run_dto_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StartRunRequestDTO.model_validate(
            {
                "workspace_id": str(uuid4()),
                "agent_version_id": str(uuid4()),
                "inputs": {"q": "hi"},
                "initiated_by": str(uuid4()),
            }
        )


@pytest.mark.anyio
async def test_admit_happy_path_replay_and_cross_actor_conflict() -> None:
    state = _InMemoryAdmissionState()
    svc, store = _build_service(state)
    t_id = str(uuid4())
    w_id = str(uuid4())
    p1 = _make_principal(tenant_id=t_id, workspace_id=w_id)
    p2 = _make_principal(tenant_id=t_id, workspace_id=w_id)
    av_id = str(uuid4())
    conv_id = str(uuid4())
    state.conversations[conv_id] = ConversationRecord(
        id=conv_id,
        tenant_id=t_id,
        workspace_id=w_id,
        external_subject=p1.user_id,
        status="active",
        created_at=datetime.now(UTC),
        last_activity_at=datetime.now(UTC),
    )

    res = await svc.admit_run(
        principal=p1,
        workspace_id=w_id,
        agent_version_id=av_id,
        inputs={"prompt": "Tóm tắt tài liệu A"},
        conversation_id=conv_id,
        idempotency_key="req-001",
    )
    assert res.status == "queued"
    assert res.replayed is False
    assert res.execution_available is False
    assert res.availability_reason == "no_executor_configured"
    assert len(state.runs) == 1
    assert len(state.jobs) == 1
    assert len(state.events) == 1
    assert len(state.audits) == 1
    assert len(state.outbox) == 1
    assert len(state.messages) == 1
    assert len(store.objects) == 1
    assert "Tóm tắt tài liệu A" not in str(state.events[0]["redacted_payload"])

    # Same actor + same intent + terminal run status -> replay preserves terminal status & creates 0 new rows.
    state.runs[(t_id, w_id, "req-001")]["status"] = "succeeded"
    replay = await svc.admit_run(
        principal=p1,
        workspace_id=w_id,
        agent_version_id=av_id,
        inputs={"prompt": "Tóm tắt tài liệu A"},
        conversation_id=conv_id,
        idempotency_key="req-001",
    )
    assert replay.run_id == res.run_id
    assert replay.status == "succeeded"
    assert replay.replayed is True
    assert len(state.jobs) == 1
    assert len(store.objects) == 1

    # Different actor with same key -> 409 conflict without disclosing run_id.
    with pytest.raises(RunAdmissionConflictError) as exc_info:
        await svc.admit_run(
            principal=p2,
            workspace_id=w_id,
            agent_version_id=av_id,
            inputs={"prompt": "Tóm tắt tài liệu A"},
            conversation_id=conv_id,
            idempotency_key="req-001",
        )
    assert res.run_id not in str(exc_info.value)

    # Same actor with different inputs -> 409 conflict.
    with pytest.raises(RunAdmissionConflictError) as exc_diff:
        await svc.admit_run(
            principal=p1,
            workspace_id=w_id,
            agent_version_id=av_id,
            inputs={"prompt": "Khác nội dung"},
            conversation_id=conv_id,
            idempotency_key="req-001",
        )
    assert res.run_id not in str(exc_diff.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "hook_stage",
    [
        "after_run_insert",
        "after_job_insert",
        "after_event_insert",
        "after_audit_insert",
        "after_outbox_insert",
    ],
)
async def test_fault_injection_rolls_back_all_rows_and_leaves_object_unactivated(
    hook_stage: str,
) -> None:
    state = _InMemoryAdmissionState()
    svc, store = _build_service(state)
    p = _make_principal()
    av_id = str(uuid4())

    async def fail_at(stage: str, _session: Any) -> None:
        if stage == hook_stage:
            raise OperationalError("SELECT 1", {}, RuntimeError("simulated DB failure"))

    with pytest.raises(RunAdmissionUnavailableError):
        await svc.admit_run(
            principal=p,
            workspace_id=p.workspace_id,
            agent_version_id=av_id,
            inputs={"q": "fault test"},
            idempotency_key=f"fault-{hook_stage}",
            after_write_hook=fail_at,
        )

    assert len(state.runs) == 0
    assert len(state.jobs) == 0
    assert len(state.events) == 0
    assert len(state.audits) == 0
    assert len(state.outbox) == 0
    assert len(store.objects) == 1  # Staged in object store, 0 DB activations


@pytest.mark.anyio
async def test_denial_paths_never_stage_object_or_write_rows() -> None:
    state = _InMemoryAdmissionState()
    svc, store = _build_service(state, max_queued=2)
    p = _make_principal()
    av_id = str(uuid4())

    # 1. External URL in attachments rejected pre-stage
    with pytest.raises(RunAdmissionValidationError):
        await svc.admit_run(
            principal=p,
            workspace_id=p.workspace_id,
            agent_version_id=av_id,
            inputs={"q": "hi"},
            attachments=[{"url": "https://evil.example/ssrf"}],
            idempotency_key="bad-att",
        )
    assert len(store.objects) == 0

    # 2. Unsealed/archived/disabled dependency rejected pre-stage
    state.version_error = RunAdmissionConflictError("Agent version is not sealed")
    with pytest.raises(RunAdmissionConflictError):
        await svc.admit_run(
            principal=p,
            workspace_id=p.workspace_id,
            agent_version_id=av_id,
            inputs={"q": "hi"},
            idempotency_key="unsealed",
        )
    assert len(store.objects) == 0
    assert len(state.runs) == 0

    # 3. Quota exceeded rejected pre-stage
    state.version_error = None
    state.active_count_override = 2
    with pytest.raises(RunAdmissionQuotaExceededError):
        await svc.admit_run(
            principal=p,
            workspace_id=p.workspace_id,
            agent_version_id=av_id,
            inputs={"q": "hi"},
            idempotency_key="quota-hit",
        )
    assert len(store.objects) == 0

    # 4. Resume always fails closed
    with pytest.raises(RunResumeDisabledError):
        await svc.resume_run(principal=p, run_id=str(uuid4()))


def test_http_endpoints_and_readiness_separation(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _InMemoryAdmissionState()
    svc, _ = _build_service(state)
    principal = _make_principal()
    av_id = str(uuid4())

    app.dependency_overrides[get_runtime_principal] = lambda: principal
    app.state.run_admission_service = svc
    test_settings = RuntimeSettings(_env_file=None, model_gateway_config_path="")
    monkeypatch.setattr("agent_runtime.main.get_settings", lambda: test_settings)
    monkeypatch.setattr("agent_runtime.api.health.get_settings", lambda: test_settings)
    monkeypatch.setattr(
        health.default_health_checker,
        "probes",
        {"fixture_db": (lambda: True, True), "semantic_cache_redis": (lambda: True, True)},
    )
    try:
        with TestClient(app) as client:
            app.state.run_admission_service = svc
            # Health ready must remain 503 when model_gateway has no capabilities (D6-03),
            # and unconfigured object store must report storage_ready=False & admission_ready=False (R06B-04).
            ready_resp = client.get("/health/ready")
            assert ready_resp.status_code == 503
            caps = ready_resp.json()["capabilities"]
            assert caps["execution_ready"] is False
            assert caps["storage_configured"] is False
            assert caps["storage_ready"] is False
            assert caps["admission_ready"] is False

            # Admission succeeds 202 queued even while execution_available is False
            start_resp = client.post(
                "/v1/runs",
                headers={"Idempotency-Key": "http-key-1"},
                json={
                    "workspace_id": principal.workspace_id,
                    "agent_version_id": av_id,
                    "inputs": {"prompt": "Hello"},
                },
            )
            assert start_resp.status_code == 202
            body = start_resp.json()
            assert body["status"] == "queued"
            assert body["replayed"] is False
            assert body["execution_available"] is False
            assert body["availability_reason"] == "no_executor_configured"

            # Metadata GET returns redacted metadata only
            meta_resp = client.get(f"/v1/runs/{body['run_id']}")
            assert meta_resp.status_code == 200
            meta = meta_resp.json()
            assert meta["run_id"] == body["run_id"]
            assert "inputs" not in meta
            assert "input_ref" not in meta

            # Resume endpoint fails closed
            resume_resp = client.post(f"/internal/v1/runs/{body['run_id']}/resume")
            assert resume_resp.status_code == 503
            assert resume_resp.json()["detail"]["code"] == "resume_disabled"
    finally:
        app.dependency_overrides.clear()
        app.state.run_admission_service = None


@pytest.mark.anyio
async def test_role_demotion_during_staging_denies_admission_and_revocation_denies_read() -> None:
    """R06B-01: Live DB role demotion during staging fails closed even with zero dependency grants."""
    state = _InMemoryAdmissionState()
    svc, store = _build_service(state)
    original_put = store.put_immutable_object

    async def _demote_during_stage(**kwargs: Any) -> Any:
        state.role = "viewer"
        return await original_put(**kwargs)

    store.put_immutable_object = _demote_during_stage  # type: ignore[method-assign]
    p = _make_principal()
    with pytest.raises(RunAdmissionAccessDeniedError):
        await svc.admit_run(
            principal=p,
            workspace_id=p.workspace_id,
            agent_version_id=str(uuid4()),
            inputs={"prompt": "Demotion test"},
            idempotency_key="demote-during-stage",
        )
    assert len(state.runs) == 0

    # Existing run can still be read as viewer (viewer grants run:read)
    state.role = "developer"
    svc2, _ = _build_service(state)
    admitted = await svc2.admit_run(
        principal=p,
        workspace_id=p.workspace_id,
        agent_version_id=str(uuid4()),
        inputs={"prompt": "Readable run"},
        idempotency_key="demote-read-check",
    )
    state.role = "viewer"
    meta = await svc2.get_run_metadata(principal=p, run_id=admitted.run_id)
    assert meta.run_id == admitted.run_id

    # Unknown / revoked live DB role fails closed on get_run_metadata as well
    state.role = "suspended"
    with pytest.raises(RunAdmissionAccessDeniedError):
        await svc2.get_run_metadata(principal=p, run_id=admitted.run_id)
