"""Shared workspace invitation contracts, email normalization, and proof encryption."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field

INVITATION_OUTBOX_TOPIC = "workspace.invitations"
INVITATION_AGGREGATE_TYPE = "workspace.invitation"
INVITATION_DELIVERY_EVENT_TYPE = "workspace.invitation.delivery_requested.v1"
INVITATION_CONSUMER_KEY = "workspace_invitation_email_v1"

INVITABLE_WORKSPACE_ROLES: frozenset[str] = frozenset({"admin", "developer", "operator", "viewer"})
INVITATION_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "revoked", "expired"})
INVITATION_DELIVERY_STATES: frozenset[str] = frozenset(
    {"queued", "sending", "provider_accepted", "failed", "unknown", "cancelled"}
)

_FORBIDDEN_EMAIL_CHARS_RE = re.compile(r"[\r\n\x00\t <>(),;\"\[\]\\]")
_LOCAL_PART_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvitationEmailError(ValueError):
    """Raised when an invitation recipient email violates strict normalization rules."""


class InvitationCryptoError(ValueError):
    """Raised when invitation proof encryption, decryption, or binding verification fails."""


def normalize_invitation_email(raw_email: str) -> str:
    """Normalize and strictly validate an ASCII recipient email address.

    Preserves dots and ``+`` tags in the local part; rejects display-name syntax,
    CR/LF/control characters, whitespace inside the address, and non-ASCII text.
    """
    if not isinstance(raw_email, str):
        raise InvitationEmailError("Email must be a string")
    if any(ch in raw_email for ch in ("\r", "\n", "\x00")):
        raise InvitationEmailError("Email contains forbidden control characters")

    trimmed = raw_email.strip()
    if not trimmed:
        raise InvitationEmailError("Email must not be empty")
    if not trimmed.isascii():
        raise InvitationEmailError("Email must be ASCII")
    if len(trimmed) > 254:
        raise InvitationEmailError("Email exceeds maximum length of 254 characters")
    if _FORBIDDEN_EMAIL_CHARS_RE.search(trimmed):
        raise InvitationEmailError("Email contains forbidden syntax or whitespace")

    normalized = trimmed.lower()
    if normalized.count("@") != 1:
        raise InvitationEmailError("Email must contain exactly one '@' separator")

    local_part, domain = normalized.split("@", 1)
    if not local_part or len(local_part) > 64:
        raise InvitationEmailError("Email local-part length is invalid")
    if not _LOCAL_PART_RE.match(local_part):
        raise InvitationEmailError("Email local-part syntax is invalid")

    if not domain or len(domain) > 253 or "." not in domain:
        raise InvitationEmailError("Email domain is invalid")

    labels = domain.split(".")
    if any(not _DOMAIN_LABEL_RE.match(label) for label in labels):
        raise InvitationEmailError("Email domain labels are invalid")
    if len(labels[-1]) < 2 or labels[-1].isdigit():
        raise InvitationEmailError("Email top-level domain is invalid")

    return normalized


def validate_public_app_origin(raw_origin: str) -> str:
    """Validate and normalize the base URL used to construct invitation links."""
    if not isinstance(raw_origin, str):
        raise InvitationCryptoError("Public application origin must be a string")
    origin = raw_origin.strip().rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InvitationCryptoError("INVITATION_PUBLIC_APP_ORIGIN must be an http(s) origin")
    if parsed.username or parsed.password:
        raise InvitationCryptoError("INVITATION_PUBLIC_APP_ORIGIN must not embed userinfo")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.params:
        raise InvitationCryptoError("INVITATION_PUBLIC_APP_ORIGIN must not include path, query, or fragment")
    return origin


def build_invitation_accept_url(public_app_origin: str, invitation_id: UUID | str, proof: str) -> str:
    """Construct the accept link with the proof isolated inside the URL fragment."""
    origin = validate_public_app_origin(public_app_origin)
    resolved_id = str(UUID(str(invitation_id)))
    if not proof or not proof.strip() or any(ch.isspace() for ch in proof):
        raise InvitationCryptoError("Invitation proof must be a non-empty URL-safe token")
    return f"{origin}/invitations/{resolved_id}/accept#token={proof}"


def hash_invitation_proof(proof: str) -> str:
    """Return the lowercase SHA-256 hex digest for an invitation proof."""
    if not isinstance(proof, str) or not proof:
        raise InvitationCryptoError("Invitation proof must be non-empty")
    return hashlib.sha256(proof.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InvitationProofBinding:
    """Immutable canonical context bound to a single invitation proof generation."""

    invitation_id: str
    tenant_id: str
    workspace_id: str
    generation: int
    recipient_issuer: str
    recipient_email_normalized: str
    role: str

    def __post_init__(self) -> None:
        UUID(self.invitation_id)
        UUID(self.tenant_id)
        UUID(self.workspace_id)
        if self.generation < 1:
            raise InvitationCryptoError("Invitation generation must be >= 1")
        if not self.recipient_issuer or not self.recipient_issuer.strip():
            raise InvitationCryptoError("Recipient issuer must be non-empty")
        if self.role not in INVITABLE_WORKSPACE_ROLES:
            raise InvitationCryptoError("Invitation role is not invitable")
        normalized = normalize_invitation_email(self.recipient_email_normalized)
        if normalized != self.recipient_email_normalized:
            raise InvitationCryptoError("Recipient email in binding must already be normalized")


class InvitationProofProtector:
    """Mint, hash, encrypt, decrypt, and context-verify temporary invitation proofs."""

    def __init__(self, fernet_key: str, key_id: str = "v1") -> None:
        if not isinstance(fernet_key, str) or not fernet_key.strip():
            raise InvitationCryptoError("Invitation proof encryption key must be configured")
        if not isinstance(key_id, str) or not key_id.strip():
            raise InvitationCryptoError("Invitation proof key_id must be non-empty")
        try:
            key_bytes = fernet_key.strip().encode("ascii")
            self._fernet = Fernet(key_bytes)
        except (UnicodeEncodeError, ValueError) as exc:
            raise InvitationCryptoError("Invitation proof encryption key must be a valid Fernet key") from exc
        self._key_id = key_id.strip()

    @property
    def key_id(self) -> str:
        return self._key_id

    @staticmethod
    def mint_proof() -> tuple[str, str]:
        """Generate a high-entropy URL-safe proof and its SHA-256 hex digest."""
        proof = secrets.token_urlsafe(32)
        return proof, hash_invitation_proof(proof)

    def encrypt_proof(self, proof: str, binding: InvitationProofBinding) -> tuple[str, str]:
        """Encrypt the raw proof together with its exact bound metadata."""
        if not proof or not proof.strip():
            raise InvitationCryptoError("Cannot encrypt an empty proof")
        envelope = {
            "v": 1,
            "key_id": self._key_id,
            "proof": proof,
            "invitation_id": binding.invitation_id,
            "tenant_id": binding.tenant_id,
            "workspace_id": binding.workspace_id,
            "generation": binding.generation,
            "recipient_issuer": binding.recipient_issuer,
            "recipient_email_normalized": binding.recipient_email_normalized,
            "role": binding.role,
        }
        serialized = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        token = self._fernet.encrypt(serialized).decode("ascii")
        return token, self._key_id

    def decrypt_and_verify_proof(
        self,
        ciphertext: str,
        stored_key_id: str | None,
        expected: InvitationProofBinding,
        *,
        expected_token_hash: str | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        """Decrypt temporary proof ciphertext and fail closed on any context or hash mismatch."""
        if not isinstance(ciphertext, str) or not ciphertext:
            raise InvitationCryptoError("Missing invitation proof ciphertext")
        if stored_key_id != self._key_id:
            raise InvitationCryptoError("Invitation proof key_id does not match active key")
        try:
            raw_bytes = ciphertext.encode("ascii")
            plaintext = self._fernet.decrypt(raw_bytes, ttl=ttl_seconds)
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError, InvalidToken, json.JSONDecodeError) as exc:
            raise InvitationCryptoError("Invitation proof ciphertext could not be verified") from exc

        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise InvitationCryptoError("Unsupported invitation proof envelope version")
        if payload.get("key_id") != self._key_id:
            raise InvitationCryptoError("Invitation proof payload key_id mismatch")

        expected_fields = {
            "invitation_id": expected.invitation_id,
            "tenant_id": expected.tenant_id,
            "workspace_id": expected.workspace_id,
            "generation": expected.generation,
            "recipient_issuer": expected.recipient_issuer,
            "recipient_email_normalized": expected.recipient_email_normalized,
            "role": expected.role,
        }
        for field_name, expected_value in expected_fields.items():
            if payload.get(field_name) != expected_value:
                raise InvitationCryptoError(f"Invitation proof binding mismatch for {field_name}")

        proof = payload.get("proof")
        if not isinstance(proof, str) or len(proof) < 32:
            raise InvitationCryptoError("Invitation proof payload missing valid proof token")

        if expected_token_hash is not None:
            actual_hash = hash_invitation_proof(proof)
            if not hmac.compare_digest(actual_hash, expected_token_hash):
                raise InvitationCryptoError("Invitation proof digest mismatch")

        return proof


class InvitationDeliveryRequestedV1(BaseModel):
    """Schema-strict non-sensitive outbox payload for invitation email delivery."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["workspace.invitation.delivery_requested.v1"] = INVITATION_DELIVERY_EVENT_TYPE
    schema_version: Literal[1] = 1
    invitation_id: UUID
    generation: int = Field(ge=1)
    tenant_id: UUID
    workspace_id: UUID
    delivery_requested_by_user_id: UUID
    requested_at: datetime

