"""Application service for run event history, cursor validation, and durable timeline management (06-C)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_admission import RunAdmissionAccessDeniedError
from agent_runtime.application.run_events import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    MAX_SAFE_INTEGER,
    RunEventAccessDeniedError,
    RunEventCursorAheadError,
    RunEventDTO,
    RunEventError,
    RunEventHistoryInconsistentError,
    RunEventHistoryPrunedError,
    RunEventNotFoundError,
    RunEventUnavailableError,
    RunEventValidationError,
    RunHistoryResponseDTO,
    map_row_to_run_event_dto,
    parse_and_validate_cursor,
    resolve_sse_cursor,
)
from agent_runtime.auth import RuntimePrincipal, has_effective_runtime_scope
from agent_runtime.persistence.database import RuntimeDatabaseUnavailable
from agent_runtime.persistence.run_admission_repository import RunAdmissionRepository
from agent_runtime.persistence.run_event_repository import RunEventRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[str, str, str | None], AbstractAsyncContextManager[AsyncSession]]


class RunEventService:
    """Orchestrates event history pagination, strict cursor logic, and authority guards."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        event_repository: RunEventRepository | None = None,
        admission_repository: RunAdmissionRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repo = event_repository if event_repository is not None else RunEventRepository()
        self._admission_repo = (
            admission_repository if admission_repository is not None else RunAdmissionRepository()
        )

    def _verify_resource_authority(
        self,
        *,
        principal: RuntimePrincipal,
        caller_role: str,
        initiated_by: str | None,
        run_id: str,
    ) -> None:
        """Enforce D6-CD01 unified authority: creator or (Owner, Admin, Operator).

        Developers and Viewers reading peer runs receive 404 (hidden).
        """
        is_creator = initiated_by is not None and str(UUID(initiated_by)) == str(
            UUID(principal.user_id)
        )
        if is_creator:
            return

        role = caller_role.lower()
        if role not in ("owner", "admin", "operator"):
            raise RunEventNotFoundError(f"Run '{run_id}' not found", code="run_not_found")

    async def verify_stream_resource_authority(
        self, *, principal: RuntimePrincipal, run_id: str
    ) -> None:
        """Recheck ownership and live role before releasing an already buffered frame."""
        try:
            async with self._session_factory(
                principal.tenant_id, principal.workspace_id, principal.user_id
            ) as session:
                role = await self._admission_repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(role, principal, "run:read"):
                    raise RunEventAccessDeniedError(
                        "Runtime permission revoked", code="authorization_revoked"
                    )
                snapshot = await self._repo.get_run_authority(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=run_id,
                )
                if snapshot is None:
                    raise RunEventNotFoundError()
                self._verify_resource_authority(
                    principal=principal,
                    caller_role=role,
                    initiated_by=snapshot["initiated_by"],
                    run_id=run_id,
                )
        except RunAdmissionAccessDeniedError as exc:
            raise RunEventAccessDeniedError(
                "Workspace authorization revoked", code="authorization_revoked"
            ) from exc
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            raise RunEventUnavailableError() from exc

    @staticmethod
    def _check_snapshot(snapshot: dict[str, Any]) -> None:
        head, oldest = snapshot["head"], snapshot["oldest"]
        if (
            not 0 <= head <= MAX_SAFE_INTEGER
            or not 1 <= oldest <= head + 1
            or not snapshot.get("tail_consistent", True)
        ):
            raise RunEventHistoryInconsistentError()

    @staticmethod
    def _check_batch(rows: list[dict[str, Any]], *, head: int, cursor: int, limit: int) -> None:
        if len(rows) != min(limit, head - cursor) or any(
            row["sequence"] != cursor + index + 1 for index, row in enumerate(rows)
        ):
            raise RunEventHistoryInconsistentError()

    async def get_history(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        last_event_id: str | int | None = 0,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> RunHistoryResponseDTO:
        """Fetch keyset-paginated historical events with strict cursor and gap verification."""
        principal.require_scope("run:read")
        try:
            r_uuid = str(UUID(run_id))
        except (ValueError, TypeError) as exc:
            raise RunEventValidationError(
                "Invalid run_id UUID format",
                code="invalid_uuid",
            ) from exc

        cursor_seq = parse_and_validate_cursor(last_event_id)
        if type(limit) is not int or limit < 1 or limit > MAX_HISTORY_LIMIT:
            raise RunEventValidationError(
                f"limit must be between 1 and {MAX_HISTORY_LIMIT}",
                code="invalid_limit",
            )

        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._admission_repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:read"):
                    raise RunEventAccessDeniedError(
                        "Active workspace role lacks run:read permission",
                        code="insufficient_scope",
                    )

                snapshot = await self._repo.get_run_snapshot(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=r_uuid,
                )
                if snapshot is None:
                    raise RunEventNotFoundError(f"Run '{run_id}' not found", code="run_not_found")

                self._verify_resource_authority(
                    principal=principal,
                    caller_role=caller_role,
                    initiated_by=snapshot["initiated_by"],
                    run_id=run_id,
                )

                self._check_snapshot(snapshot)
                head = snapshot["head"]
                oldest = snapshot["oldest"]
                prefix_floor = snapshot["prefix_floor"]

                # Strict cursor checks (D6-CD03)
                if cursor_seq > head:
                    raise RunEventCursorAheadError(
                        f"Requested cursor {cursor_seq} exceeds head sequence {head}",
                        code="cursor_ahead",
                    )
                if cursor_seq < prefix_floor:
                    raise RunEventHistoryPrunedError(
                        f"Requested cursor {cursor_seq} precedes oldest retained sequence {oldest}",
                        oldest_available_sequence=oldest,
                        head_sequence=head,
                        code="history_pruned",
                    )

                rows = await self._repo.fetch_events_batch(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=r_uuid,
                    cursor_seq=cursor_seq,
                    limit=limit,
                    head_sequence=head,
                )

                self._check_batch(rows, head=head, cursor=cursor_seq, limit=limit)

                # Contiguity and gap verification
                if rows:
                    expected_seq = cursor_seq + 1
                    for row in rows:
                        seq = int(row["sequence"])
                        if seq != expected_seq:
                            raise RunEventHistoryInconsistentError(
                                f"Sequence gap detected: expected {expected_seq}, found {seq}",
                                code="event_history_inconsistent",
                            )
                        expected_seq += 1
                elif cursor_seq < head:
                    # Head sequence indicates events exist beyond cursor, but rows are missing!
                    raise RunEventHistoryInconsistentError(
                        f"Event history tail missing between cursor {cursor_seq} and head {head}",
                        code="event_history_inconsistent",
                    )

                events: list[RunEventDTO] = [
                    map_row_to_run_event_dto(
                        row,
                        workspace_id=principal.workspace_id,
                        run_id=r_uuid,
                        default_trace_id=snapshot["trace_id"],
                    )
                    for row in rows
                ]

                next_cursor = str(events[-1].sequence) if events else str(cursor_seq)
                has_more = int(next_cursor) < head

                return RunHistoryResponseDTO(
                    run_id=r_uuid,
                    status=snapshot["status"],
                    events=events,
                    next_cursor=next_cursor,
                    has_more=has_more,
                    oldest_available_sequence=oldest,
                    head_sequence=head,
                )
        except RunEventError:
            raise
        except RunAdmissionAccessDeniedError as exc:
            raise RunEventAccessDeniedError(
                "Active workspace membership required", code="access_denied"
            ) from exc
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning("Event history lookup failed (%s)", type(exc).__name__)
            raise RunEventUnavailableError(
                "Event history store temporarily unavailable",
                code="backend_unavailable",
            ) from exc

    async def get_stream_preflight(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        query_cursor: str | None,
        header_cursor: str | None,
    ) -> tuple[int, int, int, str, str | None, list[RunEventDTO]]:
        """Preflight authorization, cursor validation, and first batch fetch before SSE headers are committed."""
        principal.require_scope("run:read")
        try:
            r_uuid = str(UUID(run_id))
        except (ValueError, TypeError) as exc:
            raise RunEventValidationError(
                "Invalid run_id UUID format",
                code="invalid_uuid",
            ) from exc

        cursor_seq = resolve_sse_cursor(
            query_cursor=query_cursor,
            header_cursor=header_cursor,
        )

        try:
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                caller_role = await self._admission_repo.verify_active_workspace_membership(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    for_share=False,
                )
                if not has_effective_runtime_scope(caller_role, principal, "run:read"):
                    raise RunEventAccessDeniedError(
                        "Active workspace role lacks run:read permission",
                        code="insufficient_scope",
                    )

                snapshot = await self._repo.get_run_snapshot(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=r_uuid,
                )
                if snapshot is None:
                    raise RunEventNotFoundError(f"Run '{run_id}' not found", code="run_not_found")

                self._verify_resource_authority(
                    principal=principal,
                    caller_role=caller_role,
                    initiated_by=snapshot["initiated_by"],
                    run_id=run_id,
                )

                self._check_snapshot(snapshot)
                head = snapshot["head"]
                oldest = snapshot["oldest"]
                prefix_floor = snapshot["prefix_floor"]

                if cursor_seq > head:
                    raise RunEventCursorAheadError(
                        f"Requested cursor {cursor_seq} exceeds head sequence {head}",
                        code="cursor_ahead",
                    )
                if cursor_seq < prefix_floor:
                    raise RunEventHistoryPrunedError(
                        f"Requested cursor {cursor_seq} precedes oldest retained sequence {oldest}",
                        oldest_available_sequence=oldest,
                        head_sequence=head,
                        code="history_pruned",
                    )

                initial_batch: list[RunEventDTO] = []
                if cursor_seq < head:
                    rows = await self._repo.fetch_events_batch(
                        session,
                        tenant_id=principal.tenant_id,
                        workspace_id=principal.workspace_id,
                        run_id=r_uuid,
                        cursor_seq=cursor_seq,
                        limit=50,
                        head_sequence=head,
                    )
                    self._check_batch(rows, head=head, cursor=cursor_seq, limit=50)
                    if rows:
                        expected_seq = cursor_seq + 1
                        for row in rows:
                            seq = int(row["sequence"])
                            if seq != expected_seq:
                                raise RunEventHistoryInconsistentError(
                                    f"Sequence gap detected: expected {expected_seq}, found {seq}",
                                    code="event_history_inconsistent",
                                )
                            expected_seq += 1
                    else:
                        raise RunEventHistoryInconsistentError(
                            f"Event history tail missing between cursor {cursor_seq} and head {head}",
                            code="event_history_inconsistent",
                        )
                    initial_batch = [
                        map_row_to_run_event_dto(
                            row,
                            workspace_id=principal.workspace_id,
                            run_id=r_uuid,
                            default_trace_id=snapshot["trace_id"],
                        )
                        for row in rows
                    ]

                return (
                    cursor_seq,
                    head,
                    oldest,
                    snapshot["status"],
                    snapshot["trace_id"],
                    initial_batch,
                )
        except RunEventError:
            raise
        except RunAdmissionAccessDeniedError as exc:
            raise RunEventAccessDeniedError(
                "Active workspace membership required", code="access_denied"
            ) from exc
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning("Stream preflight failed (%s)", type(exc).__name__)
            raise RunEventUnavailableError(
                "Event store temporarily unavailable",
                code="backend_unavailable",
            ) from exc

    async def fetch_next_events(
        self,
        *,
        principal: RuntimePrincipal,
        run_id: str,
        cursor_seq: int,
        limit: int = 50,
        default_trace_id: str | None = None,
    ) -> tuple[str, list[RunEventDTO]]:
        """Fetch next batch of events in a short closed transaction (no transaction held during wait/yield)."""
        try:
            await self.verify_stream_resource_authority(principal=principal, run_id=run_id)
            async with self._session_factory(
                principal.tenant_id,
                principal.workspace_id,
                principal.user_id,
            ) as session:
                snapshot = await self._repo.get_run_snapshot(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=run_id,
                )
                if snapshot is None:
                    raise RunEventNotFoundError(f"Run '{run_id}' not found", code="run_not_found")

                rows = await self._repo.fetch_events_batch(
                    session,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    run_id=run_id,
                    cursor_seq=cursor_seq,
                    limit=limit,
                    head_sequence=snapshot["head"],
                )

                self._check_snapshot(snapshot)
                self._check_batch(rows, head=snapshot["head"], cursor=cursor_seq, limit=limit)

                if rows:
                    expected_seq = cursor_seq + 1
                    for row in rows:
                        seq = int(row["sequence"])
                        if seq != expected_seq:
                            raise RunEventHistoryInconsistentError(
                                f"Sequence gap detected: expected {expected_seq}, found {seq}",
                                code="event_history_inconsistent",
                            )
                        expected_seq += 1

                events = [
                    map_row_to_run_event_dto(
                        row,
                        workspace_id=principal.workspace_id,
                        run_id=run_id,
                        default_trace_id=default_trace_id or snapshot["trace_id"],
                    )
                    for row in rows
                ]
                return snapshot["status"], events
        except RunEventError:
            raise
        except RunAdmissionAccessDeniedError as exc:
            raise RunEventAccessDeniedError(
                "Active workspace membership required", code="access_denied"
            ) from exc
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable, OSError) as exc:
            logger.warning("Fetch next events failed (%s)", type(exc).__name__)
            raise RunEventUnavailableError(
                "Event store temporarily unavailable",
                code="backend_unavailable",
            ) from exc

    async def append_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        event_type: str,
        data: dict[str, Any],
        trace_id: str | None = None,
    ) -> RunEventDTO:
        """Append an event within the caller's transaction."""
        return await self._repo.append_run_event(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            run_id=run_id,
            event_type=event_type,
            data=data,
            trace_id=trace_id,
        )
