from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.database import get_db_session
from control_api.core.settings import settings
from platform_common.principal import AuthenticatedPrincipal


ROLE_PERMISSIONS: dict[str, list[str]] = {
    "owner": ["agent:*", "workflow:*", "approval:*", "tool:*", "skill:*", "mcp:*"],
    "admin": ["agent:*", "workflow:*", "approval:*", "tool:*", "skill:*", "mcp:*"],
    "developer": ["agent:read", "agent:create", "agent:edit", "run:start", "run:read", "skill:read"],
    "operator": ["agent:read", "run:start", "run:read", "approval:read"],
    "viewer": ["agent:read", "run:read", "skill:read"],
}


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


def _decode_and_verify_jwt(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise _unauthorized("Malformed bearer token")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _decode_segment(encoded_header)
    payload = _decode_segment(encoded_payload)
    if header.get("alg") != "HS256" or header.get("typ") not in (None, "JWT"):
        raise _unauthorized("Unsupported bearer token algorithm")

    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected = hmac.new(
        settings.jwt_secret_key.get_secret_value().encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    try:
        supplied = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
    except ValueError as exc:
        raise _unauthorized("Malformed bearer token signature") from exc
    if not hmac.compare_digest(expected, supplied):
        raise _unauthorized("Invalid bearer token signature")

    now = int(time.time())
    if payload.get("iss") != settings.jwt_issuer:
        raise _unauthorized("Invalid bearer token issuer")
    audience = payload.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    if not isinstance(audiences, list) or settings.jwt_audience not in audiences:
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


async def get_current_principal(
    workspace_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_db_session),
) -> AuthenticatedPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Missing bearer token")
    payload = _decode_and_verify_jwt(authorization.removeprefix("Bearer ").strip())
    user_id = _required_uuid_claim(payload, "sub")
    tenant_id = _required_uuid_claim(payload, "tenant_id")
    token_workspace_id = _required_uuid_claim(payload, "workspace_id")
    try:
        normalized_workspace_id = str(UUID(workspace_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found") from exc
    if normalized_workspace_id != token_workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace scope denied")

    try:
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant_id, true), "
                "set_config('app.workspace_id', :workspace_id, true)"
            ),
            {"tenant_id": tenant_id, "workspace_id": token_workspace_id},
        )
        membership = (
            await session.execute(
                text(
                    "SELECT role FROM iam.memberships "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND workspace_id = CAST(:workspace_id AS uuid) "
                    "AND user_id = CAST(:user_id AS uuid) AND status = 'active'"
                ),
                {"tenant_id": tenant_id, "workspace_id": token_workspace_id, "user_id": user_id},
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity store unavailable",
        ) from exc
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active workspace membership required")

    role = str(membership)
    permissions = ROLE_PERMISSIONS.get(role)
    if permissions is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported workspace role")
    return AuthenticatedPrincipal(
        user_id=user_id,
        workspace_id=token_workspace_id,
        tenant_id=tenant_id,
        roles=[role],
        permissions=permissions,
    )
