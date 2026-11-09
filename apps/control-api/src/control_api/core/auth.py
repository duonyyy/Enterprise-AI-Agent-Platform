from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.database import get_db_session
from control_api.core.settings import settings
from control_api.modules.identity.session import SessionData, session_manager

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "owner": ["agent:*", "workflow:*", "approval:*", "tool:*", "skill:*", "mcp:*"],
    "admin": ["agent:*", "workflow:*", "approval:*", "tool:*", "skill:*", "mcp:*"],
    "developer": ["agent:read", "agent:create", "agent:edit", "run:start", "run:read", "skill:read"],
    "operator": ["agent:read", "run:start", "run:read", "approval:read"],
    "viewer": ["agent:read", "run:read", "skill:read"],
}


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
            "sub": session_data.user_id,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "original_iss": session_data.issuer,
            "original_sub": session_data.subject,
            "iat": session_data.created_at,
            "idle_exp": session_data.idle_expires_at,
            "exp": session_data.absolute_expires_at,
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
    # If no session cookie is sent, request is Bearer-only -> exempt from browser CSRF
    if not request.cookies.get(settings.session_cookie_name):
        return

    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-site request rejected for cookie-authenticated session",
        )

    allowed = set(settings.allowed_origins) | set(settings.oidc_redirect_allowlist)
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
        # Check if it was a session token
        if payload.get("type") == "session":
            session_data = session_manager.verify_session_token(token)
            return AuthenticatedUserContext(
                user_id=user_id,
                session_data=session_data,
                issuer=session_data.issuer,
                subject=session_data.subject,
            )
        # Scoped access token acting as bearer: session_data is None (confinement enforced at /token)
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
    session: AsyncSession = Depends(get_db_session),
) -> AuthenticatedPrincipal:
    try:
        normalized_workspace_id = str(UUID(workspace_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc

    # Case A: Bearer token provided
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        payload = _decode_and_verify_jwt(token)
        user_id = _required_uuid_claim(payload, "sub")

        # Check if bearer token is a session token or a scoped API token
        if payload.get("type") == "session":
            # Session token acting as bearer: Uniform session validation (Finding R04)
            session_data = session_manager.verify_session_token(token)
            return await _resolve_principal_from_user_id(
                session=session,
                user_id=session_data.user_id,
                workspace_id=normalized_workspace_id,
            )

        # Scoped API token: check token_workspace_id matches path workspace_id
        token_workspace_id = _required_uuid_claim(payload, "workspace_id")
        tenant_id = _required_uuid_claim(payload, "tenant_id")
        if normalized_workspace_id != token_workspace_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace scope denied")

        return await _verify_membership_and_build_principal(
            session=session,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=normalized_workspace_id,
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
    """Resolve tenant_id, role, and active membership for a user in a given workspace (Finding R03)."""
    try:
        try:
            stmt_func = text("SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)")
            row = (
                await session.execute(stmt_func, {"user_id": user_id, "workspace_id": workspace_id})
            ).mappings().one_or_none()
        except SQLAlchemyError:
            stmt = text(
                "SELECT tenant_id, role "
                "FROM iam.memberships "
                "WHERE workspace_id = CAST(:workspace_id AS uuid) "
                "  AND user_id = CAST(:user_id AS uuid) "
                "  AND status = 'active'"
            )
            row = (
                await session.execute(stmt, {"workspace_id": workspace_id, "user_id": user_id})
            ).mappings().one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity store unavailable",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    tenant_id = str(row["tenant_id"])
    return await _verify_membership_and_build_principal(
        session=session,
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


async def _verify_membership_and_build_principal(
    session: AsyncSession,
    user_id: str,
    tenant_id: str,
    workspace_id: str,
) -> AuthenticatedPrincipal:
    try:
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant_id, true), "
                "set_config('app.workspace_id', :workspace_id, true)"
            ),
            {"tenant_id": tenant_id, "workspace_id": workspace_id},
        )
        membership = (
            await session.execute(
                text(
                    "SELECT role FROM iam.memberships "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND workspace_id = CAST(:workspace_id AS uuid) "
                    "AND user_id = CAST(:user_id AS uuid) AND status = 'active'"
                ),
                {"tenant_id": tenant_id, "workspace_id": workspace_id, "user_id": user_id},
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity store unavailable",
        ) from exc

    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required",
        )

    role = str(membership)
    permissions = ROLE_PERMISSIONS.get(role)
    if permissions is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported workspace role")

    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        roles=[role],
        permissions=permissions,
    )
