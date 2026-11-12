"""Pure cryptographic and token-binding primitives for invitation handoffs (ADR-020)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

from control_api.modules.workspaces.models.errors import InvitationConfigurationError
from control_api.modules.workspaces.models.records import DecryptedHandoffPayload
from cryptography.fernet import Fernet, InvalidToken
from platform_common.invitations import hash_invitation_proof

_BROWSER_COOKIE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def hash_browser_binding_token(browser_token: str) -> str:
    """Compute canonical SHA-256 hex digest of an opaque browser handoff cookie."""
    return hashlib.sha256(browser_token.encode("utf-8")).hexdigest()


def is_valid_browser_binding_token(value: str | None) -> bool:
    """Return True if cookie value matches minimum 256-bit urlsafe entropy shape."""
    return isinstance(value, str) and bool(_BROWSER_COOKIE_RE.match(value))


def generate_browser_binding_token() -> str:
    """Generate a high-entropy 256-bit urlsafe browser binding secret."""
    return secrets.token_urlsafe(32)


class InvitationHandoffProtector:
    """Symmetric authenticated Fernet protector dedicated solely to pre-auth invitation handoffs."""

    def __init__(self, *, fernet_key: str, key_id: str = "v1") -> None:
        if not key_id or not _KEY_ID_RE.match(key_id):
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff key_id is invalid",
            )
        cleaned = fernet_key.strip()
        if not cleaned or "replace-with" in cleaned.lower():
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff encryption key is missing or placeholder",
            )
        try:
            self._fernet = Fernet(cleaned.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise InvitationConfigurationError(
                "invitation_handoff_configuration_invalid",
                "Invitation handoff encryption key must be a valid Fernet key",
            ) from exc
        self.key_id = key_id

    def encrypt_handoff(
        self,
        *,
        invitation_id: str,
        raw_proof: str,
        browser_binding_digest: str,
        issued_at: int,
        expires_at: int,
    ) -> str:
        """Encrypt bound invitation handoff payload; raw proof is hashed immediately."""
        token_hash = hash_invitation_proof(raw_proof)
        payload = {
            "v": 1,
            "invitation_id": invitation_id,
            "token_hash": token_hash,
            "browser_binding_digest": browser_binding_digest,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encrypted: str = str(self._fernet.encrypt(serialized).decode("ascii"))
        return encrypted

    def decrypt_handoff(
        self,
        *,
        ciphertext: str,
        expected_invitation_id: str,
        expected_browser_digest: str,
        now: int,
    ) -> DecryptedHandoffPayload | None:
        """Verify ciphertext integrity, expiry, and browser/invitation binding."""
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("ascii"))
            data = json.loads(plaintext.decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            return None

        if not isinstance(data, dict) or data.get("v") != 1:
            return None

        inv_id = data.get("invitation_id")
        token_hash = data.get("token_hash")
        digest = data.get("browser_binding_digest")
        issued_at = data.get("issued_at")
        expires_at = data.get("expires_at")
        if (
            not isinstance(inv_id, str)
            or not isinstance(token_hash, str)
            or len(token_hash) != 64
            or not isinstance(digest, str)
            or type(issued_at) is not int
            or type(expires_at) is not int
        ):
            return None

        if not hmac.compare_digest(inv_id, expected_invitation_id):
            return None
        if not hmac.compare_digest(digest, expected_browser_digest):
            return None
        if now >= expires_at:
            return None

        return DecryptedHandoffPayload(
            invitation_id=inv_id,
            token_hash=token_hash,
            browser_binding_digest=digest,
            issued_at=issued_at,
            expires_at=expires_at,
        )


__all__ = [
    "InvitationHandoffProtector",
    "generate_browser_binding_token",
    "hash_browser_binding_token",
    "is_valid_browser_binding_token",
]
