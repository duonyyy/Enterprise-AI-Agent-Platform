from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_runtime.settings import RuntimeSettings, get_settings


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    subject: str
    user_id: str
    tenant_id: str
    workspace_id: str
    roles: tuple[str, ...]
    scopes: frozenset[str]

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient_scope")


_bearer = HTTPBearer(auto_error=False)


def _decode_segment(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_token_encoding") from exc


def _as_uuid_claim(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"missing_{name}")
    return str(UUID(value))


def verify_runtime_token(token: str, settings: RuntimeSettings | None = None) -> RuntimePrincipal:
    active_settings = settings or get_settings()
    if len(active_settings.runtime_jwt_secret) < 32:
        raise ValueError("runtime_jwt_secret_not_configured")

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("invalid_token")
    encoded_header, encoded_payload, encoded_signature = parts
    header = json.loads(_decode_segment(encoded_header))
    payload = json.loads(_decode_segment(encoded_payload))
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise ValueError("unsupported_token_algorithm")
    if not isinstance(payload, dict):
        raise ValueError("invalid_token_payload")

    signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected = hmac.new(active_settings.runtime_jwt_secret.encode("utf-8"), signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _decode_segment(encoded_signature)):
        raise ValueError("invalid_token_signature")

    now = int(time.time())
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= now:
        raise ValueError("token_expired")
    nbf = payload.get("nbf")
    if isinstance(nbf, int) and nbf > now:
        raise ValueError("token_not_yet_valid")
    if active_settings.runtime_jwt_issuer and payload.get("iss") != active_settings.runtime_jwt_issuer:
        raise ValueError("invalid_token_issuer")
    audience = payload.get("aud")
    audiences = {audience} if isinstance(audience, str) else set(audience or [])
    if active_settings.runtime_jwt_audience not in audiences:
        raise ValueError("invalid_token_audience")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("missing_subject")
    roles_value = payload.get("roles", [])
    scopes_value = payload.get("scopes", [])
    if not isinstance(roles_value, list) or not all(isinstance(item, str) for item in roles_value):
        raise ValueError("invalid_roles")
    if not isinstance(scopes_value, list) or not all(isinstance(item, str) for item in scopes_value):
        raise ValueError("invalid_scopes")

    return RuntimePrincipal(
        subject=subject,
        user_id=_as_uuid_claim(payload, "user_id"),
        tenant_id=_as_uuid_claim(payload, "tenant_id"),
        workspace_id=_as_uuid_claim(payload, "workspace_id"),
        roles=tuple(roles_value),
        scopes=frozenset(scopes_value),
    )


async def get_runtime_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RuntimePrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    try:
        return verify_runtime_token(credentials.credentials)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_bearer_token") from None


CurrentRuntimePrincipal = Annotated[RuntimePrincipal, Depends(get_runtime_principal)]
