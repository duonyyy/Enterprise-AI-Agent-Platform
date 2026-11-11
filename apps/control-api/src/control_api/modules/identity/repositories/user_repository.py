"""PostgreSQL data access repository for identity and authorization (SPEC-AUTH-001).

Encapsulates SQL queries and projections against iam.users and workspace functions.
Preserves caller transaction boundaries, RLS context, and fail-closed repository errors.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from control_api.modules.identity.models.errors import AuthorizationDatabaseUnavailableError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _authz_unavailable(exc: Exception) -> AuthorizationDatabaseUnavailableError:
    logger.warning(
        "Authorization database projection or lookup failed (%s)",
        type(exc).__name__,
    )
    return AuthorizationDatabaseUnavailableError("Authorization service temporarily unavailable")


class UserRepository:
    """PostgreSQL repository for user records and workspace authorization."""

    async def get_by_issuer_subject(
        self,
        session: AsyncSession,
        issuer: str,
        subject: str,
    ) -> dict[str, Any] | None:
        """Lookup user by exact issuer and subject."""
        stmt = text(
            "SELECT id, issuer, subject, display_name, email_normalized "
            "FROM iam.users "
            "WHERE issuer = :issuer AND subject = :subject"
        )
        try:
            row = (
                (await session.execute(stmt, {"issuer": issuer, "subject": subject}))
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc
        return dict(row) if row is not None else None

    async def update_user(
        self,
        session: AsyncSession,
        user_id: Any,
        display_name: str | None,
        email_normalized: str | None,
    ) -> None:
        """Update display_name and/or email_normalized for an existing user."""
        stmt = text(
            "UPDATE iam.users "
            "SET display_name = COALESCE(:display_name, display_name), "
            "    email_normalized = COALESCE(:email_normalized, email_normalized) "
            "WHERE id = :id"
        )
        try:
            await session.execute(
                stmt,
                {
                    "id": user_id,
                    "display_name": display_name,
                    "email_normalized": email_normalized,
                },
            )
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc

    async def create_user(
        self,
        session: AsyncSession,
        issuer: str,
        subject: str,
        display_name: str | None,
        email_normalized: str | None,
    ) -> dict[str, Any]:
        """Insert and return a newly provisioned iam.users record."""
        stmt = text(
            "INSERT INTO iam.users (issuer, subject, display_name, email_normalized) "
            "VALUES (:issuer, :subject, :display_name, :email_normalized) "
            "RETURNING id, issuer, subject, display_name, email_normalized"
        )
        try:
            new_row = (
                (
                    await session.execute(
                        stmt,
                        {
                            "issuer": issuer,
                            "subject": subject,
                            "display_name": display_name,
                            "email_normalized": email_normalized,
                        },
                    )
                )
                .mappings()
                .one()
            )
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc
        return dict(new_row)

    async def get_by_id(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> dict[str, Any] | None:
        """Fetch internal user record by UUID."""
        stmt = text(
            "SELECT id, issuer, subject, display_name, email_normalized "
            "FROM iam.users "
            "WHERE id = :id"
        )
        try:
            row = (await session.execute(stmt, {"id": user_id})).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc
        return dict(row) if row is not None else None

    async def get_user_workspaces(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> list[dict[str, Any]]:
        """Fetch all workspace memberships for user via iam.get_user_workspaces projection."""
        stmt = text("SELECT * FROM iam.get_user_workspaces(:user_id)")
        try:
            rows = (await session.execute(stmt, {"user_id": user_id})).mappings().all()
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc
        return [dict(r) for r in rows]

    async def resolve_user_workspace_membership(
        self,
        session: AsyncSession,
        user_id: UUID,
        workspace_id: UUID,
    ) -> dict[str, Any] | None:
        """Resolve workspace membership via iam.resolve_user_workspace_membership projection."""
        stmt = text("SELECT * FROM iam.resolve_user_workspace_membership(:user_id, :workspace_id)")
        try:
            row = (
                (await session.execute(stmt, {"user_id": user_id, "workspace_id": workspace_id}))
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise _authz_unavailable(exc) from exc
        return dict(row) if row is not None else None


user_repository = UserRepository()
