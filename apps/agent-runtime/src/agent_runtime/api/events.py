from __future__ import annotations

import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agent_runtime.auth import (
    CurrentRuntimePrincipal,
    RuntimeStreamAuthError,
    revalidate_runtime_authorization,
)
from agent_runtime.persistence.database import RuntimeDatabaseUnavailable, scoped_session
from agent_runtime.settings import get_settings

router = APIRouter(tags=["events"])

_TERMINAL_RUN_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out"}
)


def _format_sse_error(code: str, message: str) -> str:
    return f"event: error\ndata: {json.dumps({'code': code, 'message': message})}\n\n"


@router.get("/v1/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    last_event_id: int = Query(default=0, ge=0),
) -> StreamingResponse:
    principal.require_scope("run:read")
    try:
        run_uuid = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run_id UUID format",
        ) from exc

    settings = get_settings()
    max_auth_age = min(settings.sse_max_auth_age_seconds, 5.0)
    lookup_timeout = min(settings.sse_auth_lookup_timeout_seconds, max_auth_age * 0.5)
    poll_interval = min(
        settings.sse_revalidation_interval_seconds,
        max(0.05, max_auth_age - lookup_timeout - 0.1),
    )

    async def event_generator():
        current_principal = principal
        last_verified_at = time.monotonic()
        cursor_seq = last_event_id

        async def _verify_fresh_auth() -> bool:
            nonlocal current_principal, last_verified_at
            elapsed = time.monotonic() - last_verified_at
            remaining_lease = max(0.05, max_auth_age - elapsed)
            effective_timeout = min(lookup_timeout, remaining_lease)
            try:
                current_principal = await revalidate_runtime_authorization(
                    current_principal,
                    required_scope="run:read",
                    session_factory=scoped_session,
                    settings=settings,
                    timeout_seconds=effective_timeout,
                )
                last_verified_at = time.monotonic()
                return True
            except RuntimeStreamAuthError as auth_err:
                yield_msg = _format_sse_error(auth_err.code, auth_err.message)
                _pending_error.append(yield_msg)
                return False

        _pending_error: list[str] = []

        # Short independent read transaction: verify run existence and initial status.
        try:
            async with asyncio.timeout(lookup_timeout):
                async with scoped_session(
                    current_principal.tenant_id,
                    current_principal.workspace_id,
                    current_principal.user_id,
                ) as session:
                    run_row = (
                        (
                            await session.execute(
                                text("""
                                    SELECT id, status FROM runtime.runs
                                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                                      AND workspace_id = CAST(:workspace_id AS uuid)
                                      AND id = CAST(:run_id AS uuid)
                                """),
                                {
                                    "tenant_id": current_principal.tenant_id,
                                    "workspace_id": current_principal.workspace_id,
                                    "run_id": run_uuid,
                                },
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
        except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable):
            yield _format_sse_error(
                "authorization_unavailable",
                "Run stream database unavailable",
            )
            return

        if not run_row:
            yield _format_sse_error("run_not_found", "Run not found")
            return

        yield f": connected to run {run_id}\n\n"

        while True:
            if not await _verify_fresh_auth():
                yield _pending_error.pop(0)
                return

            # Query next events in a short closed DB transaction (no idle lock held).
            try:
                async with asyncio.timeout(lookup_timeout):
                    async with scoped_session(
                        current_principal.tenant_id,
                        current_principal.workspace_id,
                        current_principal.user_id,
                    ) as session:
                        status_row = (
                            (
                                await session.execute(
                                    text("""
                                        SELECT status FROM runtime.runs
                                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                                          AND workspace_id = CAST(:workspace_id AS uuid)
                                          AND id = CAST(:run_id AS uuid)
                                    """),
                                    {
                                        "tenant_id": current_principal.tenant_id,
                                        "workspace_id": current_principal.workspace_id,
                                        "run_id": run_uuid,
                                    },
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        events_rows = (
                            (
                                await session.execute(
                                    text("""
                                        SELECT sequence, event_type, redacted_payload, created_at
                                        FROM runtime.run_events
                                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                                          AND workspace_id = CAST(:workspace_id AS uuid)
                                          AND run_id = CAST(:run_id AS uuid)
                                          AND sequence > :cursor_seq
                                        ORDER BY sequence ASC
                                        LIMIT 50
                                    """),
                                    {
                                        "tenant_id": current_principal.tenant_id,
                                        "workspace_id": current_principal.workspace_id,
                                        "run_id": run_uuid,
                                        "cursor_seq": cursor_seq,
                                    },
                                )
                            )
                            .mappings()
                            .all()
                        )
            except (TimeoutError, SQLAlchemyError, RuntimeDatabaseUnavailable):
                yield _format_sse_error(
                    "authorization_unavailable",
                    "Run stream database unavailable",
                )
                return

            if status_row is None:
                yield _format_sse_error("run_not_found", "Run not found")
                return

            for event in events_rows:
                if not await _verify_fresh_auth():
                    yield _pending_error.pop(0)
                    return

                payload = event["redacted_payload"]
                payload_str = payload if isinstance(payload, str) else json.dumps(payload)
                cursor_seq = int(event["sequence"])
                yield f"id: {cursor_seq}\nevent: {event['event_type']}\ndata: {payload_str}\n\n"

            run_status = str(status_row["status"])
            if not events_rows and run_status in _TERMINAL_RUN_STATUSES:
                return

            if not events_rows:
                yield ": keepalive\n\n"
                sleep_budget = min(
                    poll_interval,
                    max(0.05, max_auth_age - (time.monotonic() - last_verified_at) - lookup_timeout),
                )
                await asyncio.sleep(sleep_budget)
            else:
                # Drain any remaining queued events immediately after short yield.
                await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
