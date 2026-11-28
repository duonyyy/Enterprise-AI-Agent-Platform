"""Unit tests for runtime execution authority grants (DATA-07, DEC-07, ADR-026)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from agent_runtime.application.execution.grant import (
    DEFAULT_KEY_ID,
    ExecutionGrant,
    ExecutionGrantTamperError,
    ExecutionKeyUnavailableError,
    build_grant_aad,
    decrypt_execution_grant,
    encrypt_execution_grant,
    normalize_encryption_key,
)


def _sample_grant(
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    run_id: str | None = None,
    scopes: tuple[str, ...] | None = None,
) -> tuple[ExecutionGrant, str, str, str]:
    t_id = tenant_id or str(uuid4())
    w_id = workspace_id or str(uuid4())
    r_id = run_id or str(uuid4())
    now = datetime.now(UTC)
    grant = ExecutionGrant(
        issuer="agent-runtime-admission",
        subject=f"usr:{uuid4()}",
        user_id=str(uuid4()),
        tenant_id=t_id,
        workspace_id=w_id,
        run_id=r_id,
        parent_session_id=str(uuid4()),
        parent_rotation_version=1,
        original_scopes=scopes if scopes is not None else ("runs:write", "agents:read"),
        granted_at=now.isoformat(),
        authority_expires_at=(now + timedelta(hours=24)).isoformat(),
    )
    return grant, t_id, w_id, r_id


def test_grant_roundtrip_encryption_decryption() -> None:
    grant, t_id, w_id, r_id = _sample_grant()
    key = os.urandom(32)

    ciphertext, key_id, expires_at = encrypt_execution_grant(grant, key=key)
    assert isinstance(ciphertext, bytes)
    assert len(ciphertext) > 28  # nonce(12) + tag(16) + payload
    assert key_id == DEFAULT_KEY_ID
    assert expires_at == datetime.fromisoformat(grant.authority_expires_at)

    decrypted = decrypt_execution_grant(
        ciphertext,
        key=key,
        tenant_id=t_id,
        workspace_id=w_id,
        run_id=r_id,
    )
    assert decrypted.issuer == grant.issuer
    assert decrypted.subject == grant.subject
    assert decrypted.user_id == grant.user_id
    assert decrypted.tenant_id == t_id
    assert decrypted.workspace_id == w_id
    assert decrypted.run_id == r_id
    assert decrypted.parent_session_id == grant.parent_session_id
    assert decrypted.parent_rotation_version == grant.parent_rotation_version
    assert decrypted.original_scopes == grant.original_scopes
    assert decrypted.granted_at == grant.granted_at
    assert decrypted.authority_expires_at == grant.authority_expires_at


def test_normalize_encryption_key() -> None:
    # 32 raw bytes
    raw_32 = os.urandom(32)
    assert normalize_encryption_key(raw_32) == raw_32

    # 64 hex characters
    hex_key = raw_32.hex()
    assert normalize_encryption_key(hex_key) == raw_32

    # 32-char ASCII string
    ascii_32 = "0123456789abcdef0123456789abcdef"
    assert normalize_encryption_key(ascii_32) == ascii_32.encode("utf-8")

    # Invalid lengths or empty
    with pytest.raises(ExecutionKeyUnavailableError, match="empty or not configured"):
        normalize_encryption_key("")

    with pytest.raises(ExecutionKeyUnavailableError, match="must be 32 bytes or 64 hex"):
        normalize_encryption_key("too-short")

    with pytest.raises(ExecutionKeyUnavailableError, match="must be exactly 32 bytes"):
        normalize_encryption_key(b"too-short")


def test_grant_aad_mismatch_fails_closed() -> None:
    grant, t_id, w_id, r_id = _sample_grant()
    key = os.urandom(32)
    ciphertext, _, _ = encrypt_execution_grant(grant, key=key)

    # Different tenant
    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            ciphertext,
            key=key,
            tenant_id=str(uuid4()),
            workspace_id=w_id,
            run_id=r_id,
        )

    # Different workspace
    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            ciphertext,
            key=key,
            tenant_id=t_id,
            workspace_id=str(uuid4()),
            run_id=r_id,
        )

    # Different run
    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            ciphertext,
            key=key,
            tenant_id=t_id,
            workspace_id=w_id,
            run_id=str(uuid4()),
        )


def test_grant_tampered_ciphertext_fails_closed() -> None:
    grant, t_id, w_id, r_id = _sample_grant()
    key = os.urandom(32)
    ciphertext, _, _ = encrypt_execution_grant(grant, key=key)
    tampered = bytearray(ciphertext)

    # Corrupt last byte
    tampered[-1] ^= 0xFF
    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            bytes(tampered),
            key=key,
            tenant_id=t_id,
            workspace_id=w_id,
            run_id=r_id,
        )


def test_grant_wrong_key_fails_closed() -> None:
    grant, t_id, w_id, r_id = _sample_grant()
    key1 = os.urandom(32)
    key2 = os.urandom(32)
    ciphertext, _, _ = encrypt_execution_grant(grant, key=key1)

    with pytest.raises(ExecutionGrantTamperError):
        decrypt_execution_grant(
            ciphertext,
            key=key2,
            tenant_id=t_id,
            workspace_id=w_id,
            run_id=r_id,
        )


def test_build_grant_aad_format() -> None:
    t_id = str(uuid4())
    w_id = str(uuid4())
    r_id = str(uuid4())
    aad = build_grant_aad(tenant_id=t_id, workspace_id=w_id, run_id=r_id)
    assert aad.startswith(b"purpose=runtime_execution;tenant_id=")
    assert b"version=1" in aad
