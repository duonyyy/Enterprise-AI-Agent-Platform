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

import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from control_api.core.auth import ROLE_PERMISSIONS
from control_api.core.settings import settings
from control_api.modules.identity.crypto import sign_jwt_hs256
from control_api.modules.identity.dto import TokenResponseDTO, WorkspaceMembershipDTO
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from control_api.modules.identity.session import SessionData


class IdentityService:
    """Manages identity lookup, safe provisioning, workspace discovery, and token issuance."""

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
        stmt_find = text(
            "SELECT id, issuer, subject, display_name, email_normalized "
            "FROM iam.users "
            "WHERE issuer = :issuer AND subject = :subject"
        )
        row = (
            await session.execute(stmt_find, {"issuer": issuer, "subject": subject})
        ).mappings().one_or_none()

        if row is not None:
            user_dict = dict(row)
            # Update display_name or email if changed
            if (
                clean_display_name != user_dict["display_name"]
                or email_normalized != user_dict["email_normalized"]
            ):
                stmt_update = text(
                    "UPDATE iam.users "
                    "SET display_name = COALESCE(:display_name, display_name), "
                    "    email_normalized = COALESCE(:email_normalized, email_normalized) "
                    "WHERE id = :id"
                )
                await session.execute(
                    stmt_update,
                    {
                        "id": user_dict["id"],
                        "display_name": clean_display_name,
                        "email_normalized": email_normalized,
                    },
                )
                user_dict["display_name"] = clean_display_name or user_dict["display_name"]
                user_dict["email_normalized"] = email_normalized or user_dict["email_normalized"]
            return user_dict

        # 2. Safe Provisioning of new user
        stmt_insert = text(
            "INSERT INTO iam.users (issuer, subject, display_name, email_normalized) "
            "VALUES (:issuer, :subject, :display_name, :email_normalized) "
            "RETURNING id, issuer, subject, display_name, email_normalized"
        )
        new_row = (
            await session.execute(
                stmt_insert,
                {
                    "issuer": issuer,
                    "subject": subject,
                    "display_name": clean_display_name,
                    "email_normalized": email_normalized,
                },
            )
        ).mappings().one()

        return dict(new_row)

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

        stmt = text(
            "SELECT id, issuer, subject, display_name, email_normalized "
            "FROM iam.users "
            "WHERE id = :id"
        )
        row = (await session.execute(stmt, {"id": uid})).mappings().one_or_none()
        return dict(row) if row else None

    async def get_user_workspaces(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> list[WorkspaceMembershipDTO]:
        """Fetch all active workspaces where user has active membership (Finding R03)."""
        try:
            uid = UUID(user_id)
        except ValueError:
            return []

        # Try executing DB projection function if available
        try:
            stmt_func = text("SELECT * FROM iam.get_user_workspaces(:user_id)")
            rows = (await session.execute(stmt_func, {"user_id": uid})).mappings().all()
        except SQLAlchemyError:
            # Fallback to direct projection query
            stmt = text(
                "SELECT "
                "    t.id AS tenant_id, "
                "    t.slug AS tenant_slug, "
                "    t.name AS tenant_name, "
                "    w.id AS workspace_id, "
                "    w.slug AS workspace_slug, "
                "    w.name AS workspace_name, "
                "    m.role AS role "
                "FROM iam.memberships m "
                "JOIN iam.workspaces w ON m.tenant_id = w.tenant_id AND m.workspace_id = w.id "
                "JOIN iam.tenants t ON m.tenant_id = t.id "
                "WHERE m.user_id = :user_id "
                "  AND m.status = 'active' "
                "  AND w.status = 'active' "
                "  AND t.status = 'active' "
                "ORDER BY t.name, w.name"
            )
            rows = (await session.execute(stmt, {"user_id": uid})).mappings().all()

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
        ]

    async def issue_workspace_token(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
        session_data: SessionData | None = None,
    ) -> TokenResponseDTO:
        """Issue a short-lived workspace-scoped access token compatible with Control API and Agent Runtime.

        Confinement (Finding R05):
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

        # Query membership and tenant status
        try:
            stmt_func = text("SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)")
            row = (
                await session.execute(stmt_func, {"user_id": uid, "workspace_id": wid})
            ).mappings().one_or_none()
        except SQLAlchemyError:
            stmt = text(
                "SELECT m.tenant_id, m.role, t.status AS tenant_status, w.status AS workspace_status "
                "FROM iam.memberships m "
                "JOIN iam.workspaces w ON m.tenant_id = w.tenant_id AND m.workspace_id = w.id "
                "JOIN iam.tenants t ON m.tenant_id = t.id "
                "WHERE m.user_id = :user_id "
                "  AND m.workspace_id = :workspace_id "
                "  AND m.status = 'active'"
            )
            row = (await session.execute(stmt, {"user_id": uid, "workspace_id": wid})).mappings().one_or_none()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active workspace membership required",
            )

        if row["tenant_status"] != "active" or row["workspace_status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant or workspace is not active",
            )

        tenant_id = str(row["tenant_id"])
        role = str(row["role"])
        permissions = ROLE_PERMISSIONS.get(role, [])

        now = int(time.time())
        max_ttl = 900  # 15 minutes max short-lived token
        if session_data is not None:
            # Bound lifetime to remaining parent session lifetime (Finding R05)
            remaining_session = max(0, session_data.absolute_expires_at - now)
            ttl = min(max_ttl, remaining_session)
        else:
            ttl = max_ttl

        if ttl <= 0:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Parent session has expired; cannot issue token",
            )

        runtime_aud = settings.runtime_jwt_audience
        control_aud = settings.jwt_audience
        audiences = [control_aud, runtime_aud] if control_aud != runtime_aud else [runtime_aud]

        payload = {
            "sub": str(uid),
            "user_id": str(uid),
            "tenant_id": tenant_id,
            "workspace_id": str(wid),
            "iss": settings.runtime_jwt_issuer or settings.jwt_issuer,
            "aud": audiences,
            "roles": [role],
            "scopes": permissions,
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
        }
        if session_data is not None:
            payload["session_id"] = session_data.session_id

        # Use configured Runtime signing secret (Finding R06)
        signing_secret = settings.runtime_jwt_secret.get_secret_value()
        if not signing_secret or len(signing_secret) < 32:
            signing_secret = settings.jwt_secret_key.get_secret_value()

        token = sign_jwt_hs256(payload, signing_secret)
        return TokenResponseDTO(access_token=token, token_type="Bearer", expires_in=ttl)


identity_service = IdentityService()
