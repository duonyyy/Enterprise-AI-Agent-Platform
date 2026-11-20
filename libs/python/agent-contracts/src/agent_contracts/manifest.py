"""Canonical manifest creation, RFC8785 canonicalization, and SHA-256 digest utilities.

Authority:
- ADR-009: Immutable publishing with bundle SHA-256.
- ADR-022 (D04-02, D04-03): Deterministic manifest envelope, request hashing, and durable receipt.
- RFC 8785: JSON Canonicalization Scheme (JCS).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any
from uuid import UUID

import rfc8785


class CanonicalizationError(ValueError):
    """Raised when data cannot be canonically serialized according to RFC 8785."""


def _check_finite_numbers(data: Any) -> None:
    """Ensure floats are finite (no NaN, Infinity, -Infinity) before canonicalization."""
    if isinstance(data, float):
        if not math.isfinite(data):
            raise CanonicalizationError(f"Cannot canonicalize non-finite number: {data}")
    elif isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"Dictionary keys must be strings, got {type(key).__name__}"
                )
            _check_finite_numbers(value)
    elif isinstance(data, (list, tuple)):
        for item in data:
            _check_finite_numbers(item)


def canonicalize_json(data: Any) -> bytes:
    """Produce deterministic UTF-8 bytes conforming strictly to RFC 8785 (JCS).

    - Keys sorted by UTF-16 code units
    - Deterministic float/integer serialization
    - Minimal escaping per JSON spec
    - Disallows non-finite numbers (NaN/Infinity)
    """
    try:
        _check_finite_numbers(data)
        return rfc8785.dumps(data)
    except Exception as exc:
        if isinstance(exc, CanonicalizationError):
            raise
        raise CanonicalizationError(f"RFC8785 canonicalization failed: {exc}") from exc


def compute_bundle_sha256(manifest: dict[str, Any]) -> str:
    """Compute SHA-256 hex digest of RFC 8785 canonicalized manifest bytes."""
    canonical_bytes = canonicalize_json(manifest)
    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_request_sha256(
    *,
    operation: str = "agent:publish:v1",
    tenant_id: UUID | str,
    workspace_id: UUID | str,
    agent_id: UUID | str,
    actor_user_id: UUID | str,
    expected_revision: int,
    body: dict[str, Any] | None = None,
) -> str:
    """Compute deterministic SHA-256 hex digest of the publish request.

    Binds scope, agent, actor, operation, and expected_revision without depending on
    mutable current draft state.
    """
    envelope = {
        "operation": operation,
        "tenant_id": str(tenant_id),
        "workspace_id": str(workspace_id),
        "agent_id": str(agent_id),
        "actor_user_id": str(actor_user_id),
        "expected_revision": expected_revision,
        "body": body if body is not None else {"expected_revision": expected_revision},
    }
    canonical_bytes = canonicalize_json(envelope)
    return hashlib.sha256(canonical_bytes).hexdigest()


def build_agent_manifest(
    *,
    agent_id: UUID | str,
    slug: str,
    spec: dict[str, Any],
    model: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
    knowledge_bases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construct standard Agent version bundle manifest envelope.

    The envelope isolates canonical AgentSpec (frozen_spec) from resolved dependency snapshots.
    Temporal or runtime values (created_at, published_by, request IDs, secrets) are omitted
    to guarantee deterministic content addressing.
    """
    return {
        "schemaVersion": "1.0",
        "canonicalization": "RFC8785",
        "agentId": str(agent_id),
        "agentSlug": slug,
        "spec": spec,
        "dependencies": {
            "model": model,
            "tools": tools if tools is not None else [],
            "skills": skills if skills is not None else [],
            "knowledgeBases": knowledge_bases if knowledge_bases is not None else [],
        },
    }


__all__ = [
    "CanonicalizationError",
    "build_agent_manifest",
    "canonicalize_json",
    "compute_bundle_sha256",
    "compute_request_sha256",
]
