"""Typed run-data contracts, canonicalization, scoped refs, and object store ports (06-A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from uuid import UUID

from agent_contracts.manifest import CanonicalizationError, canonicalize_json


class RunArtifactKind(StrEnum):
    """Supported immutable object artifact kinds for run data."""

    INPUT = "input"
    OUTPUT = "output"
    MESSAGE = "message"


_VALID_KINDS: frozenset[str] = frozenset(kind.value for kind in RunArtifactKind)
DEFAULT_CONTENT_TYPE = "application/json; charset=utf-8"


class RunDataError(RuntimeError):
    """Base error for durable run-data operations."""


class RunDataValidationError(RunDataError):
    """Raised on invalid input, oversized payload, or unsafe object reference."""


class RunDataStorageUnavailableError(RunDataError):
    """Raised when object storage is unconfigured, unreachable, or fails closed."""


class RunDataNotFoundError(RunDataError):
    """Raised when a run, conversation, or object does not exist in scope."""


class RunDataIntegrityError(RunDataError):
    """Raised when object bytes fail SHA-256, size, or manifest scope verification."""


class RunDataAccessDeniedError(RunDataError):
    """Raised when caller lacks creator/scope authority to access run content (D6-01)."""


class RunDataRetentionError(RunDataError):
    """Raised when run content is expired past retention or blocked by legal hold (D6-02)."""


class RunDataConflictError(RunDataError):
    """Raised when attempting to overwrite an immutable object with different content."""


@dataclass(frozen=True, slots=True)
class ParsedObjectRef:
    """Validated server-scoped S3 object reference with durable integrity metadata."""

    ref: str
    bucket: str
    key: str
    tenant_id: str
    workspace_id: str
    run_id: str
    kind: RunArtifactKind
    expected_sha256: str
    expected_size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredObjectReceipt:
    """Receipt returned after writing and verifying an immutable object."""

    ref: str
    bucket: str
    key: str
    sha256: str
    size_bytes: int
    content_type: str


def _canonical_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as exc:
        raise RunDataValidationError(f"Invalid {field_name} UUID format") from exc


def _validate_sha256_hex(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise RunDataValidationError("Invalid SHA-256 hex digest in object reference")
    return value


def build_scoped_object_ref(
    *,
    bucket: str,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    kind: RunArtifactKind | str,
    sha256: str,
    size_bytes: int,
) -> tuple[str, str]:
    """Build a server-owned, content-bound scoped S3 URI and object key."""
    clean_bucket = bucket.strip()
    if not clean_bucket or "/" in clean_bucket or "\\" in clean_bucket or ".." in clean_bucket:
        raise RunDataValidationError("Invalid S3 bucket name")
    try:
        validated_kind = RunArtifactKind(kind)
    except ValueError as exc:
        raise RunDataValidationError(f"Unsupported artifact kind: {kind}") from exc
    if size_bytes < 0:
        raise RunDataValidationError("Object size_bytes must be non-negative")

    t_id = _canonical_uuid(tenant_id, "tenant_id")
    w_id = _canonical_uuid(workspace_id, "workspace_id")
    r_id = _canonical_uuid(run_id, "run_id")
    digest = _validate_sha256_hex(sha256)

    key = f"tenants/{t_id}/workspaces/{w_id}/runs/{r_id}/{validated_kind.value}/{digest}-{size_bytes}.json"
    return f"s3://{clean_bucket}/{key}", key


def parse_scoped_object_ref(
    ref: str,
    *,
    expected_bucket: str,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_run_id: str | None = None,
) -> ParsedObjectRef:
    """Parse and strictly validate a server-scoped S3 object reference.

    Rejects arbitrary HTTP/file URLs, path traversal, query/fragment injection,
    missing integrity bindings, and cross-tenant/cross-workspace/cross-run references.
    """
    if not isinstance(ref, str) or not ref or "\\" in ref or "\x00" in ref:
        raise RunDataValidationError("Invalid object reference")

    decoded = unquote(ref)
    if ".." in decoded or "\\" in decoded:
        raise RunDataValidationError("Path traversal is not permitted in object reference")

    parsed = urlparse(ref)
    if parsed.scheme != "s3" or parsed.query or parsed.fragment or parsed.params:
        raise RunDataValidationError("Unsupported object reference scheme or format")

    bucket = parsed.netloc
    if not bucket or bucket != expected_bucket.strip():
        raise RunDataValidationError("Object reference bucket does not match configured scope")

    key = parsed.path.lstrip("/")
    if not key or parsed.path.startswith("//"):
        raise RunDataValidationError("Invalid object reference key")

    parts = key.split("/")
    # Expected: tenants/<tid>/workspaces/<wid>/runs/<rid>/<kind>/<sha256>-<size>.json
    if (
        len(parts) != 8
        or parts[0] != "tenants"
        or parts[2] != "workspaces"
        or parts[4] != "runs"
        or parts[6] not in _VALID_KINDS
        or not parts[7].endswith(".json")
    ):
        raise RunDataValidationError("Object reference does not match canonical scoped layout")

    t_id = _canonical_uuid(parts[1], "tenant_id")
    w_id = _canonical_uuid(parts[3], "workspace_id")
    r_id = _canonical_uuid(parts[5], "run_id")

    stem = parts[7].removesuffix(".json")
    if "-" not in stem:
        raise RunDataValidationError("Object reference is missing durable sha256-size binding")
    digest_part, size_part = stem.split("-", 1)
    expected_sha256 = _validate_sha256_hex(digest_part)
    if not size_part.isdigit():
        raise RunDataValidationError("Object reference has invalid size binding")
    expected_size_bytes = int(size_part)

    if t_id != _canonical_uuid(expected_tenant_id, "expected_tenant_id"):
        raise RunDataAccessDeniedError("Object reference tenant scope mismatch")
    if w_id != _canonical_uuid(expected_workspace_id, "expected_workspace_id"):
        raise RunDataAccessDeniedError("Object reference workspace scope mismatch")
    if expected_run_id is not None and r_id != _canonical_uuid(expected_run_id, "expected_run_id"):
        raise RunDataAccessDeniedError("Object reference run scope mismatch")

    kind = RunArtifactKind(parts[6])
    return ParsedObjectRef(
        ref=f"s3://{bucket}/{key}",
        bucket=bucket,
        key=key,
        tenant_id=t_id,
        workspace_id=w_id,
        run_id=r_id,
        kind=kind,
        expected_sha256=expected_sha256,
        expected_size_bytes=expected_size_bytes,
    )


def build_input_manifest(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    initiated_by: str,
    agent_version_id: str,
    inputs: dict[str, Any],
    conversation_id: str | None = None,
    attachments: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Construct canonical immutable run input manifest (RD-02)."""
    if not isinstance(inputs, dict):
        raise RunDataValidationError("Run inputs must be a JSON object")
    manifest: dict[str, Any] = {
        "schemaVersion": "1.0",
        "kind": "run_input",
        "tenantId": _canonical_uuid(tenant_id, "tenant_id"),
        "workspaceId": _canonical_uuid(workspace_id, "workspace_id"),
        "runId": _canonical_uuid(run_id, "run_id"),
        "initiatedBy": _canonical_uuid(initiated_by, "initiated_by"),
        "agentVersionId": _canonical_uuid(agent_version_id, "agent_version_id"),
        "conversationId": _canonical_uuid(conversation_id, "conversation_id")
        if conversation_id is not None
        else None,
        "inputs": inputs,
        "attachments": list(attachments),
        "contentType": DEFAULT_CONTENT_TYPE,
    }
    if trace_id is not None:
        manifest["traceId"] = trace_id
    return manifest


def build_result_manifest(
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
    initiated_by: str,
    attempt: int,
    outcome: str,
    output: dict[str, Any],
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct canonical immutable run result manifest (RD-02)."""
    if attempt < 1:
        raise RunDataValidationError("Attempt must be >= 1")
    if not isinstance(output, dict):
        raise RunDataValidationError("Run output must be a JSON object")
    return {
        "schemaVersion": "1.0",
        "kind": "run_result",
        "tenantId": _canonical_uuid(tenant_id, "tenant_id"),
        "workspaceId": _canonical_uuid(workspace_id, "workspace_id"),
        "runId": _canonical_uuid(run_id, "run_id"),
        "initiatedBy": _canonical_uuid(initiated_by, "initiated_by"),
        "attempt": attempt,
        "outcome": outcome,
        "output": output,
        "provenance": provenance if provenance is not None else {},
        "contentType": DEFAULT_CONTENT_TYPE,
    }


def serialize_manifest(
    manifest: dict[str, Any],
    *,
    max_bytes: int,
) -> tuple[bytes, str, int]:
    """Canonicalize manifest to RFC 8785 UTF-8 bytes and enforce byte limit."""
    try:
        raw_bytes = canonicalize_json(manifest)
    except CanonicalizationError as exc:
        raise RunDataValidationError(f"Manifest canonicalization failed: {exc}") from exc

    size_bytes = len(raw_bytes)
    if size_bytes > max_bytes:
        raise RunDataValidationError(
            f"Run data payload exceeds maximum allowed size ({size_bytes} > {max_bytes} bytes)"
        )
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, digest, size_bytes


def deserialize_and_verify_manifest(
    raw_bytes: bytes,
    *,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_run_id: str,
    expected_initiated_by: str | None = None,
) -> dict[str, Any]:
    """Verify object integrity and manifest scope bindings after retrieval."""
    actual_size = len(raw_bytes)
    if expected_size_bytes is not None and actual_size != expected_size_bytes:
        raise RunDataIntegrityError("Object size mismatch")

    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RunDataIntegrityError("Object SHA-256 digest mismatch")

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunDataIntegrityError("Stored object is not valid UTF-8 JSON") from exc

    if not isinstance(payload, dict) or payload.get("schemaVersion") != "1.0":
        raise RunDataIntegrityError("Stored manifest has unsupported or missing schemaVersion")

    if (
        payload.get("tenantId") != _canonical_uuid(expected_tenant_id, "expected_tenant_id")
        or payload.get("workspaceId")
        != _canonical_uuid(expected_workspace_id, "expected_workspace_id")
        or payload.get("runId") != _canonical_uuid(expected_run_id, "expected_run_id")
    ):
        raise RunDataIntegrityError("Stored manifest scope does not match requested run")

    if expected_initiated_by is not None and payload.get("initiatedBy") != _canonical_uuid(
        expected_initiated_by, "expected_initiated_by"
    ):
        raise RunDataAccessDeniedError("Stored manifest creator does not match caller")

    return payload


class ObjectStorePort(Protocol):
    """Port for immutable scoped object storage (MinIO/S3)."""

    async def put_immutable_object(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        expected_sha256: str,
        content_type: str = DEFAULT_CONTENT_TYPE,
    ) -> StoredObjectReceipt: ...

    async def get_verified_object(
        self,
        *,
        bucket: str,
        key: str,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> bytes: ...

    async def head_object(self, *, bucket: str, key: str) -> dict[str, Any] | None: ...

    async def delete_object(self, *, bucket: str, key: str) -> bool: ...

    async def aclose(self) -> None: ...
