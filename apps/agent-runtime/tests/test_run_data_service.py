"""Unit tests for 06-A run-data contracts, scoped refs, fail-closed S3 adapter, and D6-01/02."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from agent_runtime.application.run_data import (
    RunDataAccessDeniedError,
    RunDataConflictError,
    RunDataIntegrityError,
    RunDataNotFoundError,
    RunDataRetentionError,
    RunDataStorageUnavailableError,
    RunDataValidationError,
    build_input_manifest,
    build_result_manifest,
    build_scoped_object_ref,
    deserialize_and_verify_manifest,
    parse_scoped_object_ref,
    serialize_manifest,
)
from agent_runtime.application.run_data_service import RunDataService
from agent_runtime.auth import RuntimePrincipal
from agent_runtime.persistence.object_store import S3SigV4ObjectStore, create_object_store
from agent_runtime.persistence.run_data_repository import (
    RetentionPolicyRecord,
    RunDataRecord,
)
from agent_runtime.settings import RuntimeSettings

_DUMMY_SHA = "a" * 64


def test_manifest_canonical_roundtrip_vietnamese_null_and_attachments() -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    run_id = str(uuid4())
    user_id = str(uuid4())
    agent_version_id = str(uuid4())

    manifest = build_input_manifest(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        initiated_by=user_id,
        agent_version_id=agent_version_id,
        inputs={"prompt": "Xin chào tiếng Việt — kiểm tra bền vững", "optional": None},
        attachments=[
            {"id": "att-1", "sha256": "a" * 64, "size": 128},
            {"id": "att-2", "sha256": "b" * 64, "size": 256},
        ],
    )
    raw_bytes, sha256, size_bytes = serialize_manifest(manifest, max_bytes=65_536)
    assert size_bytes == len(raw_bytes)
    assert sha256 == hashlib.sha256(raw_bytes).hexdigest()

    decoded = deserialize_and_verify_manifest(
        raw_bytes,
        expected_sha256=sha256,
        expected_size_bytes=size_bytes,
        expected_tenant_id=tenant_id,
        expected_workspace_id=workspace_id,
        expected_run_id=run_id,
        expected_initiated_by=user_id,
    )
    assert decoded["inputs"]["prompt"] == "Xin chào tiếng Việt — kiểm tra bền vững"
    assert decoded["inputs"]["optional"] is None
    assert [a["id"] for a in decoded["attachments"]] == ["att-1", "att-2"]


def test_result_manifest_contract() -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    run_id = str(uuid4())
    user_id = str(uuid4())

    manifest = build_result_manifest(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        initiated_by=user_id,
        attempt=1,
        outcome="succeeded",
        output={"answer": "Kết quả"},
        provenance={"model": "fixture"},
    )
    raw_bytes, sha256, size_bytes = serialize_manifest(manifest, max_bytes=65_536)
    decoded = deserialize_and_verify_manifest(
        raw_bytes,
        expected_sha256=sha256,
        expected_size_bytes=size_bytes,
        expected_tenant_id=tenant_id,
        expected_workspace_id=workspace_id,
        expected_run_id=run_id,
        expected_initiated_by=user_id,
    )
    assert decoded["kind"] == "run_result"
    assert decoded["attempt"] == 1


def test_scoped_ref_rejects_traversal_arbitrary_urls_and_cross_scope() -> None:
    t1 = str(uuid4())
    t2 = str(uuid4())
    w1 = str(uuid4())
    r1 = str(uuid4())

    ref, key = build_scoped_object_ref(
        bucket="agent-platform-docs",
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        kind="input",
        sha256=_DUMMY_SHA,
        size_bytes=42,
    )
    parsed = parse_scoped_object_ref(
        ref,
        expected_bucket="agent-platform-docs",
        expected_tenant_id=t1,
        expected_workspace_id=w1,
        expected_run_id=r1,
    )
    assert parsed.key == key
    assert parsed.expected_sha256 == _DUMMY_SHA
    assert parsed.expected_size_bytes == 42

    bad_refs = [
        "https://evil.example.com/payload.json",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        f"s3://agent-platform-docs/tenants/{t1}/workspaces/{w1}/runs/{r1}/input/../secret.json",
        f"s3://agent-platform-docs/tenants/{t1}/workspaces/{w1}/runs/{r1}/input/%2e%2e/secret.json",
        f"s3://agent-platform-docs/tenants/{t1}/workspaces/{w1}/runs/{r1}/input/{uuid4()}.json",
        f"s3://other-bucket/{key}",
        f"{ref}?presigned=1",
    ]
    for bad in bad_refs:
        with pytest.raises(RunDataValidationError):
            parse_scoped_object_ref(
                bad,
                expected_bucket="agent-platform-docs",
                expected_tenant_id=t1,
                expected_workspace_id=w1,
                expected_run_id=r1,
            )

    with pytest.raises(RunDataAccessDeniedError):
        parse_scoped_object_ref(
            ref,
            expected_bucket="agent-platform-docs",
            expected_tenant_id=t2,
            expected_workspace_id=w1,
            expected_run_id=r1,
        )


def test_oversized_and_non_finite_payloads_rejected() -> None:
    t1, w1, r1, u1, av1 = (str(uuid4()) for _ in range(5))
    manifest = build_input_manifest(
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        initiated_by=u1,
        agent_version_id=av1,
        inputs={"text": "x" * 2048},
    )
    with pytest.raises(RunDataValidationError, match="maximum allowed size"):
        serialize_manifest(manifest, max_bytes=512)

    bad_float_manifest = build_input_manifest(
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        initiated_by=u1,
        agent_version_id=av1,
        inputs={"val": float("nan")},
    )
    with pytest.raises(RunDataValidationError, match="canonicalization"):
        serialize_manifest(bad_float_manifest, max_bytes=65_536)


@pytest.mark.anyio
async def test_object_store_fails_closed_when_unconfigured_or_corrupt() -> None:
    unconfigured = S3SigV4ObjectStore(
        endpoint="",
        access_key="",
        secret_key="",
        bucket="agent-platform-docs",
    )
    with pytest.raises(RunDataStorageUnavailableError):
        await unconfigured.put_immutable_object(
            bucket="agent-platform-docs",
            key="tenants/a/b",
            data=b"{}",
            expected_sha256=hashlib.sha256(b"{}").hexdigest(),
        )

    def _corrupt_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=b'{"tampered":true}')
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_corrupt_handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="http://127.0.0.1:9000",
            access_key="ak",
            secret_key="sk",
            bucket="agent-platform-docs",
            client=client,
        )
        expected = hashlib.sha256(b'{"ok":true}').hexdigest()
        with pytest.raises(RunDataIntegrityError):
            await store.put_immutable_object(
                bucket="agent-platform-docs",
                key="tenants/a/b.json",
                data=b'{"ok":true}',
                expected_sha256=expected,
            )


@pytest.mark.anyio
async def test_factory_owned_http_client_closes_on_store_shutdown() -> None:
    """R06A-04: Factory-owned client closes on aclose(); borrowed client stays open."""
    settings = RuntimeSettings(
        s3_endpoint="http://127.0.0.1:9000",
        s3_access_key="ak",
        s3_secret_key="sk",
        s3_bucket="agent-platform-docs",
    )
    store = create_object_store(settings)
    client = store._client
    assert client is not None
    assert client.is_closed is False
    await store.aclose()
    assert client.is_closed is True

    borrowed = httpx.AsyncClient()
    try:
        borrowed_store = S3SigV4ObjectStore(
            endpoint="http://127.0.0.1:9000",
            access_key="ak",
            secret_key="sk",
            bucket="agent-platform-docs",
            client=borrowed,
        )
        await borrowed_store.aclose()
        assert borrowed.is_closed is False
    finally:
        await borrowed.aclose()


@pytest.mark.anyio
async def test_download_limit_is_enforced_while_reading_not_after_buffering() -> None:
    """R06A-05: Streamed GET aborts as soon as bytes exceed max_bytes."""
    bytes_yielded = 0

    class _CountedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            nonlocal bytes_yielded
            for _ in range(40):
                chunk = b"x" * 1024
                bytes_yielded += len(chunk)
                yield chunk

    def _stream_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_CountedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(_stream_handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="http://127.0.0.1:9000",
            access_key="ak",
            secret_key="sk",
            bucket="agent-platform-docs",
            client=client,
        )
        with pytest.raises(RunDataValidationError, match="max allowed size"):
            await store.get_verified_object(
                bucket="agent-platform-docs",
                key="tenants/a/b.json",
                max_bytes=1024,
            )
    assert bytes_yielded <= 2048


@pytest.mark.parametrize("put_status", [409, 412])
@pytest.mark.parametrize(
    "read_mode", ["content-length", "stream", "missing", "unavailable", "timeout"]
)
@pytest.mark.anyio
async def test_conditional_conflict_classification_preserves_storage_failures(
    put_status: int, read_mode: str
) -> None:
    """A bounded different-body conflict is distinct from unavailable/missing storage."""
    consumed = 0

    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(4):
                consumed += 1024
                yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            return httpx.Response(put_status)
        assert request.method == "GET"
        if read_mode == "timeout":
            raise httpx.ReadTimeout("Synthetic storage timeout")
        if read_mode == "missing":
            return httpx.Response(404)
        if read_mode == "unavailable":
            return httpx.Response(503)
        headers = {"Content-Length": "4096"} if read_mode == "content-length" else {}
        return httpx.Response(200, headers=headers, stream=LargeBody())

    expected_error = (
        RunDataConflictError
        if read_mode in {"content-length", "stream"}
        else RunDataNotFoundError
        if read_mode == "missing"
        else RunDataStorageUnavailableError
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = S3SigV4ObjectStore(
            endpoint="https://storage.fixture",
            access_key="fixture",
            secret_key="fixture",
            bucket="fixture-bucket",
            client=client,
        )
        with pytest.raises(expected_error):
            await store.put_immutable_object(
                bucket="fixture-bucket",
                key="existing.json",
                data=b"{}",
                expected_sha256=hashlib.sha256(b"{}").hexdigest(),
            )
    assert consumed <= (1024 if read_mode == "stream" else 0)


@pytest.mark.anyio
async def test_d6_01_creator_only_and_d6_02_retention_boundaries() -> None:
    t1, w1, r1, creator_id, admin_id, av1 = (str(uuid4()) for _ in range(6))
    ref, _ = build_scoped_object_ref(
        bucket="agent-platform-docs",
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        kind="input",
        sha256=_DUMMY_SHA,
        size_bytes=100,
    )
    finished_at = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

    class _FakeRepo:
        def __init__(self, *, legal_hold: bool = False, active_conv: bool = False) -> None:
            self.legal_hold = legal_hold
            self.active_conv = active_conv

        async def get_run_record(self, session, *, tenant_id, workspace_id, run_id):
            return RunDataRecord(
                id=r1,
                tenant_id=t1,
                workspace_id=w1,
                agent_version_id=av1,
                conversation_id=str(uuid4()),
                initiated_by=creator_id,
                external_request_key="k1",
                status="succeeded",
                started_at=finished_at - timedelta(minutes=1),
                finished_at=finished_at,
                created_at=finished_at - timedelta(minutes=2),
            )

        async def get_retention_policy(
            self, session, *, tenant_id, workspace_id, default_retention_days=30
        ):
            return RetentionPolicyRecord(
                data_class="run_content",
                retention_days=30,
                legal_hold=self.legal_hold,
            )

        async def get_run_input_ref(self, session, *, tenant_id, workspace_id, run_id):
            return ref

        async def has_active_runs_for_conversation(
            self, session, *, tenant_id, workspace_id, conversation_id
        ):
            return self.active_conv

    dummy_store = S3SigV4ObjectStore(
        endpoint="", access_key="", secret_key="", bucket="agent-platform-docs"
    )
    service = RunDataService(
        object_store=dummy_store,
        bucket="agent-platform-docs",
        repository=_FakeRepo(),  # type: ignore[arg-type]
    )

    admin_principal = RuntimePrincipal(
        subject="admin@example.com",
        user_id=admin_id,
        tenant_id=t1,
        workspace_id=w1,
        roles=("owner", "admin", "operator"),
        scopes=frozenset({"run:read", "run:*"}),
    )
    with pytest.raises(RunDataAccessDeniedError, match="human creator"):
        await service.authorize_run_input_read(None, principal=admin_principal, run_id=r1)  # type: ignore[arg-type]

    creator_principal = RuntimePrincipal(
        subject="creator@example.com",
        user_id=creator_id,
        tenant_id=t1,
        workspace_id=w1,
        roles=("developer",),
        scopes=frozenset({"run:read"}),
    )
    before_expiry = finished_at + timedelta(days=29, hours=23, minutes=59)
    auth_ok = await service.authorize_run_input_read(
        None,
        principal=creator_principal,
        run_id=r1,
        now=before_expiry,  # type: ignore[arg-type]
    )
    assert auth_ok.parsed_ref.ref == ref
    assert auth_ok.expected_sha256 == _DUMMY_SHA
    assert auth_ok.expected_size_bytes == 100

    at_expiry = finished_at + timedelta(days=30)
    with pytest.raises(RunDataRetentionError, match="expired"):
        await service.authorize_run_input_read(
            None,
            principal=creator_principal,
            run_id=r1,
            now=at_expiry,  # type: ignore[arg-type]
        )

    eval_before = await service.evaluate_run_content_retention(
        None,
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        now=before_expiry,  # type: ignore[arg-type]
    )
    assert eval_before.eligible_for_cleanup is False
    assert eval_before.reason == "within_retention_window"

    eval_after = await service.evaluate_run_content_retention(
        None,
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        now=at_expiry,  # type: ignore[arg-type]
    )
    assert eval_after.eligible_for_cleanup is True
    assert eval_after.reason == "expired_and_unheld"

    held_service = RunDataService(
        object_store=dummy_store,
        bucket="agent-platform-docs",
        repository=_FakeRepo(legal_hold=True),  # type: ignore[arg-type]
    )
    eval_held = await held_service.evaluate_run_content_retention(
        None,
        tenant_id=t1,
        workspace_id=w1,
        run_id=r1,
        now=at_expiry,  # type: ignore[arg-type]
    )
    assert eval_held.eligible_for_cleanup is False
    assert eval_held.reason == "legal_hold_active"
