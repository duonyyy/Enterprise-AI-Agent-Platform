from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.database import get_db_session, set_rls_context
from control_api.core.settings import settings
from control_api.modules.identity.models.session import SessionData
from control_api.modules.identity.services.session_service import session_manager

logger = logging.getLogger(__name__)

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "owner": [
        "workspace:*",
        "agent:*",
        "workflow:*",
        "approval:*",
        "tool:*",
        "skill:*",
        "mcp:*",
        "run:*",
        "runs:read",
        "runs:write",
        "runs:execute",
    ],
    "admin": [
        "workspace:*",
        "agent:*",
        "workflow:*",
        "approval:*",
        "tool:*",
        "skill:*",
        "mcp:*",
        "run:*",
        "runs:read",
        "runs:write",
        "runs:execute",
    ],
    "maintainer": [
        "workspace:read",
        "agent:*",
        "workflow:*",
        "approval:read",
        "tool:*",
        "skill:*",
        "mcp:*",
        "run:*",
        "runs:read",
        "runs:write",
        "runs:execute",
    ],
    "developer": [
        "workspace:read",
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
    ],
    "operator": [
        "workspace:read",
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
    ],
    "viewer": [
        "workspace:read",
        "agent:read",
        "skill:read",
        "mcp:read",
        "run:read",
        "runs:read",
    ],
}


def compute_effective_permissions(
    role_permissions: list[str],
    token_scopes: list[str] | None,
) -> list[str]:
    """Compute effective permissions from current DB role permissions intersected with token scopes (D3-B).

    A scoped token can only narrow permissions, never expand beyond the current DB role.
    """
    if token_scopes is None:
        return list(role_permissions)

    scope_set = {str(s) for s in token_scopes if isinstance(s, str)}
    effective: list[str] = []

    def _allowed_by_role(perm: str) -> bool:
        ns = perm.split(":", 1)[0]
        return perm in role_permissions or f"{ns}:*" in role_permissions

    def _allowed_by_token(perm: str) -> bool:
        ns = perm.split(":", 1)[0]
        return perm in scope_set or f"{ns}:*" in scope_set

    for perm in role_permissions:
        if perm.endswith(":*"):
            if perm in scope_set:
                effective.append(perm)
            else:
                prefix = perm[:-1]
                for s in sorted(scope_set):
                    if s.startswith(prefix) and s not in effective:
                        effective.append(s)
        elif _allowed_by_token(perm) and perm not in effective:
            effective.append(perm)

    for s in sorted(scope_set):
        if _allowed_by_role(s) and s not in effective:
            effective.append(s)

    return effective


@dataclass(frozen=True, slots=True)
class AuthenticatedUserContext:
    user_id: str
    session_data: SessionData | None
    issuer: str
    subject: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_segment(segment: str) -> dict[str, Any]:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _unauthorized("Malformed bearer token") from exc
    if not isinstance(value, dict):
        raise _unauthorized("Malformed bearer token")
    return value


def _verify_hs256_signature(signing_input: bytes, signature_segment: str, secrets: list[str]) -> bool:
    try:
        supplied = base64.urlsafe_b64decode(signature_segment + "=" * (-len(signature_segment) % 4))
    except ValueError:
        return False

    for secret in secrets:
        if not secret:
            continue
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if hmac.compare_digest(expected, supplied):
            return True
    return False


def _decode_and_verify_jwt(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise _unauthorized("Malformed bearer token")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _decode_segment(encoded_header)
    payload = _decode_segment(encoded_payload)
    if header.get("alg") != "HS256" or header.get("typ") not in (None, "JWT"):
        raise _unauthorized("Unsupported bearer token algorithm")

    # If this is a session token, delegate to uniform session_manager verification (Finding R04)
    if payload.get("type") == "session":
        session_data = session_manager.verify_session_token(token)
        return {
            "type": "session",
            "session_id": session_data.session_id,
            "ver": session_data.rotation_version,
            "sub": session_data.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": session_data.issuer,
            "original_sub": session_data.subject,
            "iat": session_data.created_at,
            "idle_exp": session_data.idle_expires_at,
            "exp": session_data.absolute_expires_at,
            "identity_evidence": session_data.identity_evidence,
        }

    # Otherwise, this is a scoped API token (signed by runtime_jwt_secret or jwt_secret_key)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    candidate_secrets = [settings.jwt_secret_key.get_secret_value()]
    runtime_sec = settings.runtime_jwt_secret.get_secret_value()
    if runtime_sec and len(runtime_sec) >= 32 and runtime_sec not in candidate_secrets:
        candidate_secrets.append(runtime_sec)

    if not _verify_hs256_signature(signing_input, encoded_signature, candidate_secrets):
        raise _unauthorized("Invalid bearer token signature")

    now = int(time.time())
    expected_issuers = {settings.jwt_issuer}
    if settings.runtime_jwt_issuer:
        expected_issuers.add(settings.runtime_jwt_issuer)
    if payload.get("iss") not in expected_issuers:
        raise _unauthorized("Invalid bearer token issuer")

    audience = payload.get("aud")
    audiences = [audience] if isinstance(audience, str) else (audience or [])
    expected_audiences = {settings.jwt_audience, settings.runtime_jwt_audience}
    if not isinstance(audiences, list) or not any(a in expected_audiences for a in audiences):
        raise _unauthorized("Invalid bearer token audience")

    if not isinstance(payload.get("exp"), (int, float)) or int(payload["exp"]) <= now:
        raise _unauthorized("Expired bearer token")
    if isinstance(payload.get("nbf"), (int, float)) and int(payload["nbf"]) > now:
        raise _unauthorized("Bearer token is not active")

    return payload


def _required_uuid_claim(payload: dict[str, Any], claim: str) -> str:
    value = payload.get(claim)
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _unauthorized(f"Bearer token is missing a valid {claim} claim") from exc


def verify_csrf_or_origin(request: Request) -> None:
    """CSRF & Origin enforcement for cookie-authenticated state-changing requests (Finding R07).

    Enforces Origin/Referer verification on state mutations when authenticated via cookie.
    Rejects untrusted same-site cross-origin requests (e.g. untrusted.example.test).
    Direct Bearer-authenticated API calls are exempt because browsers do not attach
    Authorization Bearer headers cross-site.
    """
    # If neither session cookie nor handoff cookie is sent, request is Bearer-only -> exempt from browser CSRF
    has_session_cookie = bool(request.cookies.get(settings.session_cookie_name))
    has_handoff_cookie = bool(request.cookies.get(settings.invitation_handoff_cookie_name))
    if not has_session_cookie and not has_handoff_cookie:
        return

    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-site request rejected for cookie-authenticated session",
        )

    allowed = {o.rstrip("/") for o in settings.allowed_origins} | {
        o.rstrip("/") for o in settings.oidc_redirect_allowlist
    }
    host = request.headers.get("host", "")
    if host:
        allowed.add(f"http://{host}")
        allowed.add(f"https://{host}")

    origin = request.headers.get("origin")
    if origin:
        parsed_origin = origin.rstrip("/")
        if parsed_origin not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cross-origin request rejected: Origin '{origin}' is not allowed",
            )
        return

    referer = request.headers.get("referer")
    if referer:
        parsed_ref = urlparse(referer)
        ref_origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}"
        if ref_origin not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Referer origin is not allowed for cookie-authenticated session",
            )


def verify_invitation_csrf_or_origin(request: Request) -> None:
    """Strict Origin/Referer enforcement for browser-bound invitation acceptance endpoints (ADR-020).

    Applies uniformly to BOTH unauthenticated pre-auth handoffs and authenticated
    preview/accept endpoints without delegating trust to arbitrary Host headers or
    allowing missing Origin/Referer on browser state transitions.
    """
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "csrf_origin_rejected",
                "message": "Cross-site invitation request rejected.",
            },
        )

    allowed = {o.rstrip("/") for o in settings.allowed_origins} | {
        settings.invitation_public_app_origin.rstrip("/")
    }
    origin = request.headers.get("origin")
    if origin is not None:
        parsed_origin = origin.strip().rstrip("/")
        if not parsed_origin or parsed_origin.lower() == "null" or parsed_origin not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "csrf_origin_rejected",
                    "message": "Request Origin is not allowed for invitation acceptance.",
                },
            )
        return

    referer = request.headers.get("referer")
    if referer:
        parsed_ref = urlparse(referer)
        ref_origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}"
        if not parsed_ref.scheme or not parsed_ref.netloc or ref_origin not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "csrf_origin_rejected",
                    "message": "Request Referer origin is not allowed for invitation acceptance.",
                },
            )
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "csrf_origin_rejected",
            "message": "Trusted Origin or Referer header is required for invitation requests.",
        },
    )


async def get_current_authenticated_user(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUserContext:
    """Extract authenticated user from either Session Cookie or Bearer token (no workspace required)."""
    # 1. Check Bearer Authorization header
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = _decode_and_verify_jwt(token)
        user_id = _required_uuid_claim(payload, "sub")
        # Check if it was a session token (already verified in _decode_and_verify_jwt)
        if payload.get("type") == "session":
            session_data = SessionData(
                session_id=str(payload["session_id"]),
                user_id=user_id,
                issuer=str(payload["original_iss"]),
                subject=str(payload["original_sub"]),
                created_at=int(payload["iat"]),
                idle_expires_at=int(payload["idle_exp"]),
                absolute_expires_at=int(payload["exp"]),
                rotation_version=int(payload["ver"]),
                identity_evidence=payload.get("identity_evidence"),
            )
            return AuthenticatedUserContext(
                user_id=user_id,
                session_data=session_data,
                issuer=session_data.issuer,
                subject=session_data.subject,
            )
        # Scoped access token acting as bearer: must carry a valid, active parent session_id (D3-B)
        token_session_id = _required_uuid_claim(payload, "session_id")
        session_manager.verify_parent_session_active(
            session_id=token_session_id,
            user_id=user_id,
        )
        # session_data is None so /v1/auth/token and /v1/auth/refresh reject scoped tokens
        return AuthenticatedUserContext(
            user_id=user_id,
            session_data=None,
            issuer=str(payload.get("iss", settings.jwt_issuer)),
            subject=user_id,
        )

    # 2. Check Session Cookie
    cookie_token = request.cookies.get(settings.session_cookie_name)
    if cookie_token:
        session_data = session_manager.verify_session_token(cookie_token)
        return AuthenticatedUserContext(
            user_id=session_data.user_id,
            session_data=session_data,
            issuer=session_data.issuer,
            subject=session_data.subject,
        )

    raise _unauthorized("Missing authentication credentials (session cookie or bearer token)")


async def get_current_principal(
    workspace_id: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AuthenticatedPrincipal:
    try:
        normalized_workspace_id = str(UUID(workspace_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        ) from exc

    # Case A: Bearer token provided
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = _decode_and_verify_jwt(token)
        user_id = _required_uuid_claim(payload, "sub")

        # Check if bearer token is a session token or a scoped API token
        if payload.get("type") == "session":
            # Session token acting as bearer: already verified via session_manager in _decode_and_verify_jwt
            return await _resolve_principal_from_user_id(
                session=session,
                user_id=user_id,
                workspace_id=normalized_workspace_id,
            )

        # Scoped API token: verify parent session_id is active & unrevoked in Redis (D3-B)
        token_session_id = _required_uuid_claim(payload, "session_id")
        session_manager.verify_parent_session_active(
            session_id=token_session_id,
            user_id=user_id,
        )

        # Check token_workspace_id matches path workspace_id
        token_workspace_id = _required_uuid_claim(payload, "workspace_id")
        token_tenant_id = _required_uuid_claim(payload, "tenant_id")
        if normalized_workspace_id != token_workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace scope denied",
            )

        raw_scopes = payload.get("scopes")
        token_scopes = (
            [str(s) for s in raw_scopes if isinstance(s, str)]
            if isinstance(raw_scopes, list)
            else None
        )

        return await _verify_membership_and_build_principal(
            session=session,
            user_id=user_id,
            tenant_id=token_tenant_id,
            workspace_id=normalized_workspace_id,
            token_scopes=token_scopes,
        )

    # Case B: Session Cookie provided
    cookie_token = request.cookies.get(settings.session_cookie_name)
    if cookie_token:
        session_data = session_manager.verify_session_token(cookie_token)
        return await _resolve_principal_from_user_id(
            session=session,
            user_id=session_data.user_id,
            workspace_id=normalized_workspace_id,
        )

    raise _unauthorized("Missing bearer token or session cookie")


async def _resolve_principal_from_user_id(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
) -> AuthenticatedPrincipal:
    """Resolve tenant_id, role, and active membership/workspace/tenant for a user (ADR-018 / D3-A..D3-D)."""
    return await _verify_membership_and_build_principal(
        session=session,
        user_id=user_id,
        tenant_id=None,
        workspace_id=workspace_id,
        token_scopes=None,
    )


async def _verify_membership_and_build_principal(
    session: AsyncSession,
    user_id: str,
    tenant_id: str | None,
    workspace_id: str,
    token_scopes: list[str] | None = None,
) -> AuthenticatedPrincipal:
    try:
        uid = UUID(user_id)
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        ) from exc

    stmt_func = text("SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)")
    try:
        row = (
            await session.execute(stmt_func, {"user_id": uid, "workspace_id": wid})
        ).mappings().one_or_none()
    except SQLAlchemyError as exc:
        logger.warning(
            "Workspace authorization lookup failed (%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service temporarily unavailable",
        ) from exc

    if (
        row is None
        or row.get("tenant_status", "active") != "active"
        or row.get("workspace_status", "active") != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    resolved_tenant_id = str(row["tenant_id"])
    if tenant_id is not None and resolved_tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    role = str(row["role"])
    role_permissions = ROLE_PERMISSIONS.get(role)
    if role_permissions is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    effective_permissions = compute_effective_permissions(role_permissions, token_scopes)

    try:
        await set_rls_context(
            session,
            tenant_id=resolved_tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    except SQLAlchemyError as exc:
        logger.warning(
            "Failed to bind RLS transaction context (%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service temporarily unavailable",
        ) from exc

    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_id=workspace_id,
        tenant_id=resolved_tenant_id,
        roles=[role],
        permissions=effective_permissions,
    )


@dataclass(frozen=True, slots=True)
class SessionWorkspaceContext:
    """Verified active application session + active workspace membership context."""

    principal: AuthenticatedPrincipal
    session_user: AuthenticatedUserContext


async def get_current_session_workspace_context(
    workspace_id: str,
    auth_user: Annotated[AuthenticatedUserContext, Depends(get_current_authenticated_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionWorkspaceContext:
    """Require an active application session (cookie or session Bearer JWT) and active membership."""
    if auth_user.session_data is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "active_session_required",
                "message": "Workspace management and invitations require an active application session",
            },
        )
    try:
        normalized_workspace_id = str(UUID(workspace_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_workspace_id",
                "message": "workspace_id must be a valid UUID",
            },
        ) from exc

    principal = await _resolve_principal_from_user_id(
        session=session,
        user_id=auth_user.user_id,
        workspace_id=normalized_workspace_id,
    )
    return SessionWorkspaceContext(principal=principal, session_user=auth_user)
