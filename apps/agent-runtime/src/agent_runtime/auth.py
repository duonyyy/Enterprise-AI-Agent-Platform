from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.persistence.database import (
    RuntimeDatabaseUnavailable,
    get_db_session,
    set_rls_context,
)
from agent_runtime.settings import RuntimeSettings, get_settings

logger = logging.getLogger(__name__)

RUNTIME_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": frozenset(
        {
            "agent:*",
            "workflow:*",
            "approval:*",
            "tool:*",
            "skill:*",
            "mcp:*",
            "run:*",
            "runs:*",
            "run:start",
            "run:read",
            "runs:read",
            "runs:write",
            "runs:execute",
        }
    ),
    "admin": frozenset(
        {
            "agent:*",
            "workflow:*",
            "approval:*",
            "tool:*",
            "skill:*",
            "mcp:*",
            "run:*",
            "runs:*",
            "run:start",
            "run:read",
            "runs:read",
            "runs:write",
            "runs:execute",
        }
    ),
    "maintainer": frozenset(
        {
            "agent:*",
            "workflow:*",
            "approval:read",
            "tool:*",
            "skill:*",
            "mcp:*",
            "run:*",
            "runs:*",
            "run:start",
            "run:read",
            "runs:read",
            "runs:write",
            "runs:execute",
        }
    ),
    "developer": frozenset(
        {
            "agent:read",
            "agent:create",
            "agent:edit",
            "agent:publish",
            "skill:read",
            "skill:create",
            "skill:publish",
            "mcp:read",
            "mcp:connection:manage",
            "run:start",
            "run:read",
            "runs:read",
            "runs:write",
            "runs:execute",
        }
    ),
    "operator": frozenset(
        {
            "agent:read",
            "skill:read",
            "mcp:read",
            "approval:read",
            "approval:decide",
            "run:start",
            "run:read",
            "runs:read",
            "runs:write",
            "runs:execute",
        }
    ),
    "viewer": frozenset(
        {
            "agent:read",
            "skill:read",
            "mcp:read",
            "run:read",
            "runs:read",
        }
    ),
}

_SCOPE_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "run:start": ("run:start", "runs:write", "runs:execute", "run:*", "runs:*"),
    "runs:write": ("runs:write", "run:start", "runs:execute", "run:*", "runs:*"),
    "runs:execute": ("runs:execute", "run:start", "runs:write", "run:*", "runs:*"),
    "run:read": ("run:read", "runs:read", "run:*", "runs:*"),
    "runs:read": ("runs:read", "run:read", "run:*", "runs:*"),
}


def _scope_matches(held_scopes: frozenset[str], target_scope: str) -> bool:
    candidates = _SCOPE_EQUIVALENTS.get(target_scope, (target_scope,))
    if any(c in held_scopes for c in candidates):
        return True
    if ":" in target_scope:
        ns = target_scope.split(":", 1)[0]
        if f"{ns}:*" in held_scopes:
            return True
    return False


def compute_effective_runtime_scopes(
    role: str,
    token_scopes: frozenset[str],
) -> frozenset[str]:
    """Intersect current DB role permissions with token scopes (D3-B)."""
    role_scopes = RUNTIME_ROLE_SCOPES.get(role)
    if role_scopes is None:
        return frozenset()

    effective: set[str] = set()
    for perm in role_scopes:
        if _scope_matches(token_scopes, perm):
            effective.add(perm)
    for ts in token_scopes:
        if _scope_matches(role_scopes, ts):
            effective.add(ts)
    return frozenset(effective)


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    subject: str
    user_id: str
    tenant_id: str
    workspace_id: str
    roles: tuple[str, ...]
    scopes: frozenset[str]
    session_id: str | None = None

    def require_scope(self, scope: str) -> None:
        if not _scope_matches(self.scopes, scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_scope",
            )


class ParentSessionVerifierProtocol(Protocol):
    def verify_parent_session(
        self,
        session_id: str,
        user_id: str,
        now: int | None = None,
    ) -> tuple[str, Any]: ...


class RedisParentSessionVerifier:
    """Read-only Redis verifier for parent session state and revocation (D3-B.2)."""

    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        import redis

        active = settings or get_settings()
        self._client = redis.Redis.from_url(
            active.redis_url,
            socket_timeout=active.redis_socket_timeout_seconds,
            socket_connect_timeout=active.redis_connect_timeout_seconds,
            decode_responses=True,
        )

    def verify_parent_session(
        self,
        session_id: str,
        user_id: str,
        now: int | None = None,
    ) -> tuple[str, Any]:
        import redis

        try:
            pipe = self._client.pipeline()
            pipe.get(f"revocation:session:{session_id}")
            pipe.get(f"revocation:user:{user_id}")
            pipe.get(f"auth:session:{session_id}")
            if now is None:
                pipe.time()
            results = pipe.execute()
        except (redis.RedisError, OSError) as exc:
            logger.warning("Runtime Redis session verification failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service temporarily unavailable",
            ) from exc

        sess_exp_raw = results[0]
        user_until_raw = results[1]
        raw_state = results[2]
        if now is not None:
            current = now
        else:
            redis_time = results[3]
            current = (
                int(redis_time[0])
                if isinstance(redis_time, (tuple, list)) and redis_time
                else int(time.time())
            )

        if sess_exp_raw is not None:
            try:
                if int(sess_exp_raw) >= current:
                    return "REVOKED", None
            except ValueError:
                return "REVOKED", None

        if not raw_state or not isinstance(raw_state, str):
            return "MISSING", None

        try:
            data = json.loads(raw_state)
        except (ValueError, TypeError, json.JSONDecodeError):
            return "CORRUPT", None

        if not isinstance(data, dict):
            return "CORRUPT", None

        s_id = data.get("session_id")
        u_id = data.get("user_id")
        ver = data.get("rotation_version")
        created_at = data.get("created_at")
        idle_exp = data.get("idle_expires_at")
        abs_exp = data.get("absolute_expires_at")

        if (
            s_id != session_id
            or u_id != user_id
            or type(ver) is not int
            or ver < 1
            or type(created_at) is not int
            or created_at <= 0
            or type(idle_exp) is not int
            or idle_exp <= 0
            or type(abs_exp) is not int
            or abs_exp <= 0
        ):
            return "CORRUPT", None

        if user_until_raw is not None:
            try:
                if created_at <= int(user_until_raw):
                    return "REVOKED", None
            except ValueError:
                return "REVOKED", None

        if current >= abs_exp:
            return "ABSOLUTE_EXPIRED", None

        if current >= idle_exp:
            return "IDLE_EXPIRED", None

        return "OK", data


_parent_session_verifier: ParentSessionVerifierProtocol | None = None


def set_parent_session_verifier(verifier: ParentSessionVerifierProtocol | None) -> None:
    """Override the parent session verifier (used in unit/security tests)."""
    global _parent_session_verifier
    _parent_session_verifier = verifier


def get_parent_session_verifier(
    settings: RuntimeSettings | None = None,
) -> ParentSessionVerifierProtocol:
    if _parent_session_verifier is not None:
        return _parent_session_verifier
    return RedisParentSessionVerifier(settings=settings)


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
        raise ValueError(f"missing_{name}")  # noqa: TRY004
    return str(UUID(value))


def verify_runtime_token(
    token: str,
    settings: RuntimeSettings | None = None,
    *,
    require_session_id: bool = False,
) -> RuntimePrincipal:
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
        raise ValueError("invalid_token_payload")  # noqa: TRY004

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

    session_id_raw = payload.get("session_id")
    session_id: str | None = None
    if session_id_raw is not None:
        session_id = _as_uuid_claim(payload, "session_id")
    elif require_session_id:
        raise ValueError("missing_session_id")

    return RuntimePrincipal(
        subject=subject,
        user_id=_as_uuid_claim(payload, "user_id"),
        tenant_id=_as_uuid_claim(payload, "tenant_id"),
        workspace_id=_as_uuid_claim(payload, "workspace_id"),
        roles=tuple(roles_value),
        scopes=frozenset(scopes_value),
        session_id=session_id,
    )


async def get_runtime_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RuntimePrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    try:
        token_principal = verify_runtime_token(
            credentials.credentials,
            require_session_id=True,
        )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_bearer_token",
        ) from None

    assert token_principal.session_id is not None

    # 1. Verify parent session is active and unrevoked in Redis (D3-B.2)
    verifier = get_parent_session_verifier()
    sess_status, _ = verifier.verify_parent_session(
        session_id=token_principal.session_id,
        user_id=token_principal.user_id,
    )
    if sess_status != "OK":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="parent_session_revoked_or_expired",
        )

    # 2. Verify live membership, workspace status, and tenant status in PostgreSQL (D3-B.2 / D3-C)
    stmt = text("SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)")
    try:
        row = (
            await session.execute(
                stmt,
                {
                    "user_id": UUID(token_principal.user_id),
                    "workspace_id": UUID(token_principal.workspace_id),
                },
            )
        ).mappings().one_or_none()
    except (SQLAlchemyError, RuntimeDatabaseUnavailable) as exc:
        logger.warning("Runtime workspace authorization lookup failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service temporarily unavailable",
        ) from exc

    if (
        row is None
        or row.get("tenant_status", "active") != "active"
        or row.get("workspace_status", "active") != "active"
        or str(row["tenant_id"]) != token_principal.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    db_role = str(row["role"])
    if db_role not in RUNTIME_ROLE_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    effective_scopes = compute_effective_runtime_scopes(db_role, token_principal.scopes)

    try:
        await set_rls_context(
            session,
            tenant_id=token_principal.tenant_id,
            workspace_id=token_principal.workspace_id,
            user_id=token_principal.user_id,
        )
    except SQLAlchemyError as exc:
        logger.warning("Runtime failed to bind RLS context (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service temporarily unavailable",
        ) from exc

    return RuntimePrincipal(
        subject=token_principal.subject,
        user_id=token_principal.user_id,
        tenant_id=token_principal.tenant_id,
        workspace_id=token_principal.workspace_id,
        roles=(db_role,),
        scopes=effective_scopes,
        session_id=token_principal.session_id,
    )


CurrentRuntimePrincipal = Annotated[RuntimePrincipal, Depends(get_runtime_principal)]
