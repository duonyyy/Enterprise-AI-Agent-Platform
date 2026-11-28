"""Execution grant models and scoped AES-GCM envelope encryption (DEC-07 D7-03 / D7-07)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_CIPHERTEXT_BYTES: Final[int] = 16384
NONCE_BYTES: Final[int] = 12
TAG_BYTES: Final[int] = 16
DEFAULT_KEY_ID: Final[str] = "key-exec-v1"


class ExecutionGrantError(Exception):
    """Base error for execution grant operations."""


class ExecutionGrantTamperError(ExecutionGrantError):
    """Decryption failed, AAD mismatch, or ciphertext modified."""


class ExecutionKeyUnavailableError(ExecutionGrantError):
    """Encryption key cannot be resolved."""


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """Canonical execution authority grant minted at run admission and bound to parent session."""

    issuer: str
    subject: str
    user_id: str
    tenant_id: str
    workspace_id: str
    run_id: str
    parent_session_id: str
    parent_rotation_version: int
    original_scopes: tuple[str, ...]
    granted_at: str
    authority_expires_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "parent_session_id": self.parent_session_id,
            "parent_rotation_version": self.parent_rotation_version,
            "original_scopes": list(self.original_scopes),
            "granted_at": self.granted_at,
            "authority_expires_at": self.authority_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ExecutionGrant:
        return cls(
            issuer=str(data["issuer"]),
            subject=str(data["subject"]),
            user_id=str(data["user_id"]),
            tenant_id=str(data["tenant_id"]),
            workspace_id=str(data["workspace_id"]),
            run_id=str(data["run_id"]),
            parent_session_id=str(data["parent_session_id"]),
            parent_rotation_version=int(data["parent_rotation_version"]),  # type: ignore[arg-type]
            original_scopes=tuple(str(s) for s in data.get("original_scopes", ())),  # type: ignore[union-attr]
            granted_at=str(data["granted_at"]),
            authority_expires_at=str(data["authority_expires_at"]),
        )


def build_grant_aad(*, tenant_id: str, workspace_id: str, run_id: str) -> bytes:
    """Canonical AAD binding purpose, tenant, workspace, run and version."""
    return (
        f"purpose=runtime_execution;tenant_id={UUID(tenant_id)};workspace_id={UUID(workspace_id)};run_id={UUID(run_id)};version=1"
    ).encode()


def normalize_encryption_key(raw_key: str | bytes) -> bytes:
    """Ensure key is exactly 32 bytes for AES-256-GCM."""
    if isinstance(raw_key, bytes):
        if len(raw_key) == 32:
            return raw_key
        raise ExecutionKeyUnavailableError("Encryption key must be exactly 32 bytes")
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise ExecutionKeyUnavailableError("Encryption key is empty or not configured")
    cleaned = raw_key.strip()
    if len(cleaned) == 64:
        try:
            decoded = bytes.fromhex(cleaned)
            if len(decoded) == 32:
                return decoded
        except ValueError:
            pass
    raw_bytes = cleaned.encode("utf-8")
    if len(raw_bytes) == 32:
        return raw_bytes
    raise ExecutionKeyUnavailableError(
        f"Encryption key must be 32 bytes or 64 hex characters (got {len(raw_bytes)} bytes)"
    )


def encrypt_execution_grant(
    grant: ExecutionGrant,
    key: bytes,
    key_id: str = DEFAULT_KEY_ID,
) -> tuple[bytes, str, datetime]:
    """Encrypt execution grant with fresh nonce and canonical AAD."""
    payload = json.dumps(grant.to_dict(), separators=(",", ":"), sort_keys=True).encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    aad = build_grant_aad(
        tenant_id=grant.tenant_id,
        workspace_id=grant.workspace_id,
        run_id=grant.run_id,
    )
    aesgcm = AESGCM(key)
    encrypted = aesgcm.encrypt(nonce, payload, aad)
    ciphertext = nonce + encrypted
    if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise ExecutionGrantError(
            f"Execution grant ciphertext exceeds {MAX_CIPHERTEXT_BYTES} bytes"
        )
    expires_at = datetime.fromisoformat(grant.authority_expires_at)
    return ciphertext, key_id, expires_at


def decrypt_execution_grant(
    ciphertext: bytes,
    key: bytes,
    *,
    tenant_id: str,
    workspace_id: str,
    run_id: str,
) -> ExecutionGrant:
    """Decrypt and verify execution grant with AAD and scope validation."""
    if len(ciphertext) < NONCE_BYTES + TAG_BYTES:
        raise ExecutionGrantTamperError("Ciphertext too short")
    if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise ExecutionGrantTamperError(f"Ciphertext exceeds {MAX_CIPHERTEXT_BYTES} bytes")
    nonce = ciphertext[:NONCE_BYTES]
    encrypted = ciphertext[NONCE_BYTES:]
    aad = build_grant_aad(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    try:
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, encrypted, aad)
        data = json.loads(plaintext.decode("utf-8"))
        grant = ExecutionGrant.from_dict(data)
    except Exception as exc:
        raise ExecutionGrantTamperError(
            "Grant decryption or integrity verification failed"
        ) from exc

    if (
        grant.tenant_id != str(UUID(tenant_id))
        or grant.workspace_id != str(UUID(workspace_id))
        or grant.run_id != str(UUID(run_id))
    ):
        raise ExecutionGrantTamperError("Grant scope mismatch")
    return grant
