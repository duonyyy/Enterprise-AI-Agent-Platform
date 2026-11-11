"""Identity service for user mapping, workspace discovery, and token issuance (SPEC-AUTH-001).

Adheres strictly to DB-56 schema:
- iam.users: (id, issuer, subject, display_name, email_normalized, created_at)
- iam.memberships: (tenant_id, workspace_id, user_id, role, status, joined_at)
- iam.workspaces: (tenant_id, id, slug, name, status, created_at)
- iam.tenants: (id, slug, name, status, created_at, updated_at)

Zero auto-linking across different issuers, zero unearned workspace permissions.
Token confinement & delegation signing profile aligned with Agent Runtime (Finding R05, R06).
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from control_api.core.settings import settings
from control_api.modules.identity.dto import TokenResponseDTO, WorkspaceMembershipDTO
from control_api.modules.identity.models.errors import RepositoryUnavailableError
from control_api.modules.identity.repositories.user_repository import (
    UserRepository,
    user_repository,
)
from control_api.modules.identity.security.crypto import sign_jwt_hs256
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from control_api.modules.identity.models.session import SessionData

logger = logging.getLogger(__name__)


def _translate_repository_errors[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except RepositoryUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    return wrapper


class WorkspaceAccessDeniedError(HTTPException):
    """Raised when a user lacks active membership in an active workspace and active tenant."""

    def __init__(self, detail: str = "Active workspace membership required") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class IdentityService:
    """Manages identity lookup, safe provisioning, workspace discovery, and token issuance."""

    def __init__(self, user_repo: UserRepository | None = None) -> None:
        self.user_repo = user_repo or user_repository

    @_translate_repository_errors
    async def get_or_provision_user(
        self,
        session: AsyncSession,
        issuer: str,
        subject: str,
        display_name: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Map external (issuer, subject) to internal iam.users record.

        Never links accounts by email across distinct issuers.
        Never auto-creates active workspace memberships.
        """
        email_normalized = email.strip().lower() if email and email.strip() else None
        clean_display_name = display_name.strip() if display_name and display_name.strip() else None

        # 1. Lookup user by exact (issuer, subject)
        row = await self.user_repo.get_by_issuer_subject(session, issuer, subject)
        if row is not None:
            user_dict = dict(row)
            # Update display_name or email if changed
            if (
                clean_display_name != user_dict["display_name"]
                or email_normalized != user_dict["email_normalized"]
            ):
                await self.user_repo.update_user(
                    session,
                    user_dict["id"],
                    clean_display_name,
                    email_normalized,
                )
                user_dict["display_name"] = clean_display_name or user_dict["display_name"]
                user_dict["email_normalized"] = email_normalized or user_dict["email_normalized"]
            return user_dict

        # 2. Safe Provisioning of new user
        new_row = await self.user_repo.create_user(
            session,
            issuer,
            subject,
            clean_display_name,
            email_normalized,
        )
        return dict(new_row)

    @_translate_repository_errors
    async def get_user_by_id(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Fetch internal user record by UUID."""
        try:
            uid = UUID(user_id)
        except ValueError:
            return None

        row = await self.user_repo.get_by_id(session, uid)
        return dict(row) if row else None

    @_translate_repository_errors
    async def get_user_workspaces(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> list[WorkspaceMembershipDTO]:
        """Fetch all active workspaces where user has active membership (ADR-018 / D3-A)."""
        try:
            uid = UUID(user_id)
        except ValueError:
            return []

        rows = await self.user_repo.get_user_workspaces(session, uid)
        return [
            WorkspaceMembershipDTO(
                tenant_id=str(r["tenant_id"]),
                tenant_slug=str(r["tenant_slug"]),
                tenant_name=str(r["tenant_name"]),
                workspace_id=str(r["workspace_id"]),
                workspace_slug=str(r["workspace_slug"]),
                workspace_name=str(r["workspace_name"]),
                role=str(r["role"]),
            )
            for r in rows
            if r.get("membership_status", "active") == "active"
        ]

    @_translate_repository_errors
    async def issue_workspace_token(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
        session_data: SessionData | None = None,
        *,
        email: str | None = None,
        name: str | None = None,
    ) -> TokenResponseDTO:
        """Issue a short-lived workspace-scoped access token compatible with Control API and Agent Runtime.

        Confinement (Finding R05 / D3-B):
          Requires an active parent browser session (`session_id`).
          Lifetime is bounded by 900 seconds AND remaining session absolute lifetime.
          Cannot extend lifetime beyond the parent session.

        Delegation Profile (Finding R06):
          Signed using runtime_jwt_secret if configured (min 32 chars), matching Agent Runtime verifier.
          Audience bound to runtime_jwt_audience and jwt_audience.
        """
        try:
            uid = UUID(user_id)
            wid = UUID(workspace_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid UUID format for user_id or workspace_id",
            ) from exc

        if session_data is None or not session_data.session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Active application session required to issue workspace token",
            )

        row = await self.user_repo.resolve_user_workspace_membership(session, uid, wid)

        if (
            row is None
            or row.get("tenant_status", "active") != "active"
            or row.get("workspace_status", "active") != "active"
        ):
            raise WorkspaceAccessDeniedError()

        tenant_id = str(row["tenant_id"])
        role = str(row["role"])
        from control_api.core.auth import ROLE_PERMISSIONS

        permissions = ROLE_PERMISSIONS.get(role)
        if permissions is None:
            raise WorkspaceAccessDeniedError()

        now = int(time.time())
        max_ttl = 900  # 15 minutes max short-lived token
        remaining_session = max(0, session_data.absolute_expires_at - now)
        ttl = min(max_ttl, remaining_session)

        if ttl <= 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Parent session has expired; cannot issue token",
            )

        if email is None and name is None:
            user_row = await self.get_user_by_id(session, str(uid))
            if user_row is not None:
                raw_email = user_row.get("email_normalized")
                raw_name = user_row.get("display_name")
                email = str(raw_email) if raw_email is not None else None
                name = str(raw_name) if raw_name is not None else None

        runtime_aud = settings.runtime_jwt_audience
        control_aud = settings.jwt_audience
        audiences = [control_aud, runtime_aud] if control_aud != runtime_aud else [runtime_aud]

        payload = {
            "sub": str(uid),
            "user_id": str(uid),
            "email": email,
            "name": name,
            "tenant_id": tenant_id,
            "workspace_id": str(wid),
            "session_id": session_data.session_id,
            "iss": settings.runtime_jwt_issuer or settings.jwt_issuer,
            "aud": audiences,
            "roles": [role],
            "scopes": list(permissions),
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
        }

        # Use configured Runtime signing secret (Finding R06)
        signing_secret = settings.runtime_jwt_secret.get_secret_value()
        if not signing_secret or len(signing_secret) < 32:
            signing_secret = settings.jwt_secret_key.get_secret_value()

        token = sign_jwt_hs256(payload, signing_secret)
        return TokenResponseDTO(access_token=token, token_type="Bearer", expires_in=ttl)


identity_service = IdentityService()
