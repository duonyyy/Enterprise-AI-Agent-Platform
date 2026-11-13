"""Transactional repository for scoped workspace member management (ADR-021 / Step 03-D)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from control_api.core.database import set_rls_context
from control_api.modules.workspaces.models.enums import (
    ExpectedActiveStatus,
    MembershipRevokeOutcome,
    MembershipRoleUpdateOutcome,
    MembershipStatus,
    WorkspaceRole,
)
from control_api.modules.workspaces.models.errors import (
    MembershipConflictError,
    MembershipForbiddenError,
    MembershipNotFoundError,
    MembershipValidationError,
    WorkspaceDatabaseUnavailableError,
)
from control_api.modules.workspaces.models.ports import MembershipRepositoryPort
from control_api.modules.workspaces.models.rbac import (
    can_assign_target_role,
    can_list_workspace_members,
    can_manage_target_role,
    can_revoke_target_role,
)
from control_api.modules.workspaces.models.records import (
    MembershipMutationRecord,
    MembershipRecord,
)
from control_api.modules.workspaces.repositories._db_helpers import (
    db_unavailable,
    decode_keyset_cursor,
    encode_keyset_cursor,
    format_timestamp,
    is_active_workspace_in_tenant,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _db_unavailable(exc: Exception) -> WorkspaceDatabaseUnavailableError:
    return db_unavailable(
        exc,
        operation="membership_persistence",
        message="Workspace membership persistence service temporarily unavailable",
    )


def _format_ts(value: Any) -> str:
    return format_timestamp(value) or ""


def encode_membership_cursor(joined_at: datetime, membership_id: str) -> str:
    """Encode deterministic `(joined_at, id)` keyset cursor."""
    return encode_keyset_cursor("joined_at", joined_at, membership_id)


def decode_membership_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode and validate keyset pagination cursor."""
    return decode_keyset_cursor(
        cursor,
        ts_field="joined_at",
        error_factory=MembershipValidationError,
    )


class MembershipRepository:
    """PostgreSQL repository enforcing RLS, workspace-first lock ordering, and atomic audit logs."""

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MembershipRecord:
        raw_display = row.get("display_name")
        return MembershipRecord(
            membership_id=str(row["id"]),
            user_id=str(row["user_id"]),
            display_name=str(raw_display) if raw_display is not None else None,
            role=str(row["role"]),
            status=str(row["status"]),
            joined_at=_format_ts(row["joined_at"]),
        )

    @staticmethod
    def _to_mutation_record(
        record: MembershipRecord,
        *,
        outcome: MembershipRoleUpdateOutcome | MembershipRevokeOutcome,
        audit_event_id: str | None = None,
    ) -> MembershipMutationRecord:
        return MembershipMutationRecord(
            membership_id=record.membership_id,
            user_id=record.user_id,
            display_name=record.display_name,
            role=record.role,
            status=record.status,
            joined_at=record.joined_at,
            outcome=str(outcome),
            audit_event_id=audit_event_id,
        )

    async def _lock_workspace_and_relevant_memberships(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        target_membership_id: UUID,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
        """Acquire workspace FOR UPDATE first, then lock relevant memberships in sorted user_id order."""
        if not await is_active_workspace_in_tenant(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            lock_mode="update",
        ):
            raise MembershipForbiddenError(
                "workspace_inactive",
                "Workspace is not active or accessible",
            )

        locked_rows = (
            (
                await session.execute(
                    text(
                        "SELECT m.id, m.tenant_id, m.workspace_id, m.user_id, m.role, m.status, m.joined_at "
                        "FROM iam.memberships AS m "
                        "WHERE m.tenant_id = :tenant_id "
                        "  AND m.workspace_id = :workspace_id "
                        "  AND ("
                        "    m.id = :target_membership_id "
                        "    OR m.user_id = :actor_user_id "
                        "    OR (m.role = 'owner' AND m.status = 'active')"
                        "  ) "
                        "ORDER BY m.user_id ASC, m.id ASC "
                        "FOR UPDATE OF m"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "actor_user_id": actor_user_id,
                        "target_membership_id": target_membership_id,
                    },
                )
            )
            .mappings()
            .all()
        )
        rows = [dict(r) for r in locked_rows]
        actor_row = next((r for r in rows if str(r["user_id"]) == str(actor_user_id)), None)
        if actor_row is None or str(actor_row["status"]) != "active":
            raise MembershipForbiddenError(
                "active_membership_required",
                "Active workspace membership required",
            )
        actor_role = str(actor_row["role"])
        if not can_list_workspace_members(actor_role):
            raise MembershipForbiddenError(
                "membership_management_forbidden",
                "Only active workspace owners and admins can manage members",
            )
        target_row = next((r for r in rows if str(r["id"]) == str(target_membership_id)), None)
        return actor_row, target_row, rows

    async def _lookup_display_name(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> str | None:
        user_row = (
            (
                await session.execute(
                    text("SELECT display_name FROM iam.users WHERE id = :user_id"),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if user_row is None or user_row["display_name"] is None:
            return None
        return str(user_row["display_name"])

    async def _insert_audit_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        action: str,
        membership_id: UUID,
        request_id: str,
        trace_id: str,
        details: dict[str, Any],
    ) -> str:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO governance.audit_events ("
                        "    tenant_id, workspace_id, actor_subject, action, "
                        "    resource_type, resource_id, outcome, request_id, "
                        "    trace_id, reason_code, details_redacted"
                        ") VALUES ("
                        "    :tenant_id, :workspace_id, :actor_subject, :action, "
                        "    'membership', :resource_id, 'succeeded', :request_id, "
                        "    :trace_id, :reason_code, CAST(:details_redacted AS jsonb)"
                        ") RETURNING id"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "workspace_id": workspace_id,
                        "actor_subject": str(actor_user_id),
                        "action": action,
                        "resource_id": str(membership_id),
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "reason_code": action,
                        "details_redacted": json.dumps(details, sort_keys=True),
                    },
                )
            )
            .mappings()
            .one()
        )
        return str(row["id"])

    async def list_memberships(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
        status_filter: MembershipStatus | None,
    ) -> tuple[list[MembershipRecord], str | None]:
        """List scoped workspace memberships deterministically without exposing private user identity columns."""
        if limit < 1 or limit > 100:
            raise MembershipValidationError("invalid_limit", "limit must be between 1 and 100")

        cursor_pair = decode_membership_cursor(cursor) if cursor is not None else None

        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            if not await is_active_workspace_in_tenant(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                lock_mode="none",
            ):
                raise MembershipForbiddenError(
                    "workspace_inactive",
                    "Workspace is not active or accessible",
                )

            actor_row = (
                (
                    await session.execute(
                        text(
                            "SELECT role, status "
                            "FROM iam.memberships "
                            "WHERE tenant_id = :tenant_id "
                            "  AND workspace_id = :workspace_id "
                            "  AND user_id = :user_id"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "user_id": actor_user_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if actor_row is None or str(actor_row["status"]) != "active":
                raise MembershipForbiddenError(
                    "active_membership_required",
                    "Active workspace membership required",
                )
            if not can_list_workspace_members(str(actor_row["role"])):
                raise MembershipForbiddenError(
                    "membership_management_forbidden",
                    "Only active workspace owners and admins can list members",
                )

            where_clauses = [
                "m.tenant_id = :tenant_id",
                "m.workspace_id = :workspace_id",
            ]
            params: dict[str, Any] = {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "fetch_limit": limit + 1,
            }
            if status_filter is not None:
                where_clauses.append("m.status = :status_filter")
                params["status_filter"] = status_filter
            if cursor_pair is not None:
                where_clauses.append(
                    "(m.joined_at > :cursor_joined_at "
                    "OR (m.joined_at = :cursor_joined_at AND m.id > :cursor_id))"
                )
                params["cursor_joined_at"] = cursor_pair[0]
                params["cursor_id"] = cursor_pair[1]

            stmt = text(
                "SELECT m.id, m.user_id, u.display_name, m.role, m.status, m.joined_at "
                "FROM iam.memberships AS m "
                "LEFT JOIN iam.users AS u ON u.id = m.user_id "
                f"WHERE {' AND '.join(where_clauses)} "
                "ORDER BY m.joined_at ASC, m.id ASC "
                "LIMIT :fetch_limit"
            )
            raw_rows = (await session.execute(stmt, params)).mappings().all()
            has_more = len(raw_rows) > limit
            page_rows = [dict(r) for r in raw_rows[:limit]]
            items = [self._row_to_record(r) for r in page_rows]
            next_cursor: str | None = None
            if has_more and page_rows:
                last = page_rows[-1]
                joined_at = last["joined_at"]
                if not isinstance(joined_at, datetime):
                    joined_at = datetime.fromisoformat(str(joined_at))
                next_cursor = encode_membership_cursor(joined_at, str(last["id"]))
            return items, next_cursor
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def update_member_role(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        new_role: WorkspaceRole,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord:
        """Atomically update an active member's role with workspace-first serialization and last-owner protection."""
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            (
                actor_row,
                target_row,
                locked_rows,
            ) = await self._lock_workspace_and_relevant_memberships(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_membership_id=membership_id,
            )
            actor_role = str(actor_row["role"])

            if not can_assign_target_role(actor_role, expected_role, new_role):
                raise MembershipForbiddenError(
                    "role_management_forbidden",
                    "Actor role is not permitted to perform this role assignment",
                )

            if target_row is None:
                raise MembershipNotFoundError(
                    "membership_not_found",
                    "Workspace membership not found",
                )

            current_role = str(target_row["role"])
            current_status = str(target_row["status"])

            if not can_manage_target_role(actor_role, current_role):
                raise MembershipForbiddenError(
                    "role_management_forbidden",
                    "Actor role is not permitted to manage this member's current role",
                )

            if current_status != expected_status:
                raise MembershipConflictError(
                    "membership_conflict",
                    "Only active memberships can have their role updated",
                )
            if current_role != expected_role:
                raise MembershipConflictError(
                    "stale_state",
                    "Member role changed concurrently; refresh and confirm again",
                )

            target_user_uuid = UUID(str(target_row["user_id"]))
            display_name = await self._lookup_display_name(session, user_id=target_user_uuid)

            if current_role == new_role:
                record = self._row_to_record({**target_row, "display_name": display_name})
                return self._to_mutation_record(
                    record,
                    outcome=MembershipRoleUpdateOutcome.NO_OP,
                )

            if current_role == "owner" and new_role != "owner":
                active_owner_count = sum(
                    1
                    for row in locked_rows
                    if str(row["role"]) == "owner" and str(row["status"]) == "active"
                )
                if active_owner_count <= 1:
                    raise MembershipConflictError(
                        "last_owner_protected",
                        "Cannot demote the last active workspace owner",
                    )

            updated_row = (
                (
                    await session.execute(
                        text(
                            "UPDATE iam.memberships "
                            "SET role = :new_role "
                            "WHERE tenant_id = :tenant_id "
                            "  AND workspace_id = :workspace_id "
                            "  AND id = :membership_id "
                            "RETURNING id, tenant_id, workspace_id, user_id, role, status, joined_at"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "membership_id": membership_id,
                            "new_role": new_role,
                        },
                    )
                )
                .mappings()
                .one()
            )
            audit_event_id = await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action="workspace.membership.role_update",
                membership_id=membership_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "tenant_id": str(tenant_id),
                    "workspace_id": str(workspace_id),
                    "membership_id": str(membership_id),
                    "target_user_id": str(target_user_uuid),
                    "actor_role": actor_role,
                    "previous_role": current_role,
                    "new_role": new_role,
                },
            )
            record = self._row_to_record({**dict(updated_row), "display_name": display_name})
            return self._to_mutation_record(
                record,
                outcome=MembershipRoleUpdateOutcome.UPDATED,
                audit_event_id=audit_event_id,
            )
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    async def revoke_member(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord:
        """Atomically revoke a workspace membership, protecting the last active Owner."""
        # Replay with expected_status='active' returns ALREADY_REVOKED idempotently per ADR-021.
        _ = expected_status
        try:
            await set_rls_context(
                session,
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id),
                user_id=str(actor_user_id),
            )
            (
                actor_row,
                target_row,
                locked_rows,
            ) = await self._lock_workspace_and_relevant_memberships(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                target_membership_id=membership_id,
            )
            actor_role = str(actor_row["role"])

            if not can_revoke_target_role(actor_role, expected_role):
                raise MembershipForbiddenError(
                    "role_management_forbidden",
                    "Actor role is not permitted to revoke this target role",
                )

            if target_row is None:
                raise MembershipNotFoundError(
                    "membership_not_found",
                    "Workspace membership not found",
                )

            current_role = str(target_row["role"])
            current_status = str(target_row["status"])

            if not can_revoke_target_role(actor_role, current_role):
                raise MembershipForbiddenError(
                    "role_management_forbidden",
                    "Actor role is not permitted to revoke this member",
                )

            if current_status == "invited":
                raise MembershipConflictError(
                    "membership_conflict",
                    "Invited memberships must be revoked via workspace invitations",
                )

            if current_role != expected_role:
                raise MembershipConflictError(
                    "stale_state",
                    "Member role changed concurrently; refresh and confirm again",
                )

            target_user_uuid = UUID(str(target_row["user_id"]))
            display_name = await self._lookup_display_name(session, user_id=target_user_uuid)

            if current_status == "revoked":
                record = self._row_to_record({**target_row, "display_name": display_name})
                return self._to_mutation_record(
                    record,
                    outcome=MembershipRevokeOutcome.ALREADY_REVOKED,
                )

            if current_role == "owner":
                active_owner_count = sum(
                    1
                    for row in locked_rows
                    if str(row["role"]) == "owner" and str(row["status"]) == "active"
                )
                if active_owner_count <= 1:
                    raise MembershipConflictError(
                        "last_owner_protected",
                        "Cannot revoke the last active workspace owner",
                    )

            updated_row = (
                (
                    await session.execute(
                        text(
                            "UPDATE iam.memberships "
                            "SET status = 'revoked' "
                            "WHERE tenant_id = :tenant_id "
                            "  AND workspace_id = :workspace_id "
                            "  AND id = :membership_id "
                            "RETURNING id, tenant_id, workspace_id, user_id, role, status, joined_at"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "workspace_id": workspace_id,
                            "membership_id": membership_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            audit_event_id = await self._insert_audit_event(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action="workspace.membership.revoke",
                membership_id=membership_id,
                request_id=request_id,
                trace_id=trace_id,
                details={
                    "tenant_id": str(tenant_id),
                    "workspace_id": str(workspace_id),
                    "membership_id": str(membership_id),
                    "target_user_id": str(target_user_uuid),
                    "actor_role": actor_role,
                    "revoked_role": current_role,
                },
            )
            record = self._row_to_record({**dict(updated_row), "display_name": display_name})
            return self._to_mutation_record(
                record,
                outcome=MembershipRevokeOutcome.REVOKED,
                audit_event_id=audit_event_id,
            )
        except SQLAlchemyError as exc:
            raise _db_unavailable(exc) from exc

    def bind(self, session: AsyncSession) -> MembershipRepositoryPort:
        """Bind an AsyncSession into a session-free MembershipRepositoryPort adapter."""
        return SessionBoundMembershipRepository(repository=self, session=session)


class SessionBoundMembershipRepository:
    """Session-scoped adapter implementing MembershipRepositoryPort for pure services."""

    def __init__(self, repository: MembershipRepository, session: AsyncSession) -> None:
        self._repository = repository
        self._session = session

    async def list_memberships(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        limit: int,
        cursor: str | None,
        status_filter: MembershipStatus | None,
    ) -> tuple[list[MembershipRecord], str | None]:
        return await self._repository.list_memberships(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            limit=limit,
            cursor=cursor,
            status_filter=status_filter,
        )

    async def update_member_role(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        new_role: WorkspaceRole,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord:
        return await self._repository.update_member_role(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            new_role=new_role,
            expected_role=expected_role,
            expected_status=expected_status,
            request_id=request_id,
            trace_id=trace_id,
        )

    async def revoke_member(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_user_id: UUID,
        membership_id: UUID,
        expected_role: WorkspaceRole,
        expected_status: ExpectedActiveStatus,
        request_id: str,
        trace_id: str,
    ) -> MembershipMutationRecord:
        return await self._repository.revoke_member(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            expected_role=expected_role,
            expected_status=expected_status,
            request_id=request_id,
            trace_id=trace_id,
        )


__all__ = [
    "MembershipRepository",
    "SessionBoundMembershipRepository",
    "decode_membership_cursor",
    "encode_membership_cursor",
]
