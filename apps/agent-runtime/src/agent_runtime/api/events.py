"""Agent Runtime HTTP endpoints for RunEvent history and real bounded SSE streaming (06-C)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, Callable
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BeforeValidator
from starlette.types import Message, Receive, Scope, Send

from agent_runtime.application.run_event_service import RunEventService
from agent_runtime.application.run_events import (
    DEFAULT_HISTORY_LIMIT,
    TERMINAL_RUN_STATUSES,
    RunEventAccessDeniedError,
    RunEventCursorAheadError,
    RunEventError,
    RunEventFrameTooLargeError,
    RunEventHistoryInconsistentError,
    RunEventHistoryPrunedError,
    RunEventNotFoundError,
    RunEventStreamLimitReachedError,
    RunEventUnavailableError,
    RunEventValidationError,
    RunHistoryResponseDTO,
)
from agent_runtime.auth import (
    CurrentRuntimePrincipal,
    RuntimeStreamAuthError,
    revalidate_runtime_authorization,
)
from agent_runtime.persistence.database import scoped_session
from agent_runtime.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


class BoundedEventResponse(StreamingResponse):
    """Bound socket sends and lifetime, including when the generator is suspended."""

    def __init__(
        self,
        content: AsyncGenerator[str, None],
        *,
        send_timeout: float,
        max_duration: float,
        on_close: Callable[[], None],
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self._events = content
        self._send_timeout = send_timeout
        self._max_duration = max_duration
        self._on_close = on_close

    async def stream_response(self, send: Send) -> None:
        async def bounded_send(message: Message) -> None:
            async with asyncio.timeout(self._send_timeout):
                await send(message)

        try:
            await super().stream_response(bounded_send)
        except TimeoutError:
            # The socket is blocked: do not try to send another error frame.
            return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with asyncio.timeout(self._max_duration):
                await super().__call__(scope, receive, send)
        except TimeoutError:
            return
        finally:
            try:
                await self._events.aclose()
            finally:
                self._on_close()


def _validate_query_values(request: Request, *, sse: bool) -> None:
    for name in ("last_event_id", "limit") if not sse else ("last_event_id",):
        if len(request.query_params.getlist(name)) > 1:
            raise HTTPException(
                400, detail={"code": "invalid_cursor", "message": "Duplicate query parameter"}
            )
    if sse and len(request.headers.getlist("last-event-id")) > 1:
        raise HTTPException(
            400, detail={"code": "invalid_cursor", "message": "Duplicate cursor header"}
        )


def validate_sse_parameters(request: Request) -> None:
    _validate_query_values(request, sse=True)


def validate_history_parameters(request: Request) -> None:
    _validate_query_values(request, sse=False)


def parse_history_limit(value: Any) -> int:
    if type(value) is int and 1 <= value <= 100:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,2}", value):
        parsed = int(value)
        if parsed <= 100:
            return parsed
    raise HTTPException(400, detail={"code": "invalid_limit", "message": "Invalid history limit"})


HistoryLimit = Annotated[int, BeforeValidator(parse_history_limit), Query()]


class ConnectionCapManager:
    """Per-replica in-memory connection slot supervisor (D6-CD04)."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._total: int = 0
        self._by_workspace: dict[str, int] = {}
        self._by_user_workspace: dict[tuple[str, str, str], int] = {}

    def try_acquire(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        max_total: int,
        max_workspace: int,
        max_user_workspace: int,
    ) -> bool:
        with self._lock:
            ws_current = self._by_workspace.get(workspace_id, 0)
            user_key = (tenant_id, workspace_id, user_id)
            user_current = self._by_user_workspace.get(user_key, 0)

            if (
                self._total >= max_total
                or ws_current >= max_workspace
                or user_current >= max_user_workspace
            ):
                return False

            self._total += 1
            self._by_workspace[workspace_id] = ws_current + 1
            self._by_user_workspace[user_key] = user_current + 1
            return True

    def release(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
    ) -> None:
        with self._lock:
            if self._total > 0:
                self._total -= 1

            ws_current = self._by_workspace.get(workspace_id, 0)
            if ws_current <= 1:
                self._by_workspace.pop(workspace_id, None)
            else:
                self._by_workspace[workspace_id] = ws_current - 1

            user_key = (tenant_id, workspace_id, user_id)
            user_current = self._by_user_workspace.get(user_key, 0)
            if user_current <= 1:
                self._by_user_workspace.pop(user_key, None)
            else:
                self._by_user_workspace[user_key] = user_current - 1

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "total": self._total,
                "workspaces": len(self._by_workspace),
                "user_workspaces": len(self._by_user_workspace),
            }


_connection_caps = ConnectionCapManager()


def get_connection_caps() -> ConnectionCapManager:
    return _connection_caps


def _format_sse_error(code: str, message: str) -> str:
    """Format transport error frame without id field (D6-CD03)."""
    return f"event: error\ndata: {json.dumps({'code': code, 'message': message})}\n\n"


def _translate_run_event_error(exc: RunEventError) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if isinstance(exc, RunEventValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    if isinstance(exc, RunEventAccessDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if isinstance(exc, RunEventNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if isinstance(exc, RunEventCursorAheadError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    if isinstance(exc, RunEventHistoryPrunedError):
        detail["oldest_available_sequence"] = exc.oldest_available_sequence
        detail["head_sequence"] = exc.head_sequence
        return HTTPException(status_code=status.HTTP_410_GONE, detail=detail)
    if isinstance(exc, RunEventStreamLimitReachedError):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(exc.retry_after)},
        )
    if isinstance(
        exc,
        (RunEventHistoryInconsistentError, RunEventUnavailableError, RunEventFrameTooLargeError),
    ):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)


def build_run_event_service() -> RunEventService:
    return RunEventService(session_factory=scoped_session)


def get_run_event_service(request: Request) -> RunEventService:
    existing = getattr(request.app.state, "run_event_service", None)
    if isinstance(existing, RunEventService):
        return existing
    return build_run_event_service()


RunEventServiceDep = Annotated[RunEventService, Depends(get_run_event_service)]


@router.get(
    "/v1/runs/{run_id}/history",
    response_model=RunHistoryResponseDTO,
    response_model_exclude_none=True,
    dependencies=[Depends(validate_history_parameters)],
)
async def get_run_history(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    service: RunEventServiceDep,
    last_event_id: str | None = Query(default=None),
    limit: HistoryLimit = DEFAULT_HISTORY_LIMIT,
) -> RunHistoryResponseDTO:
    """Fetch paginated historical run events using keyset cursor and unified authority."""
    try:
        async with asyncio.timeout(min(get_settings().sse_query_timeout_seconds, 1.0)):
            return await service.get_history(
                principal=principal,
                run_id=run_id,
                last_event_id=last_event_id,
                limit=limit,
            )
    except TimeoutError as exc:
        raise _translate_run_event_error(RunEventUnavailableError()) from exc
    except RunEventError as exc:
        raise _translate_run_event_error(exc) from exc


@router.get("/v1/runs/{run_id}/events", dependencies=[Depends(validate_sse_parameters)])
async def stream_events(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    service: RunEventServiceDep,
    last_event_id: str | None = Query(default=None),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Reconnectable bounded SSE stream with live revalidation and per-replica connection caps."""
    settings = get_settings()
    cap_mgr = get_connection_caps()

    # 1. Acquire per-replica connection slot before preflight/headers
    acquired = cap_mgr.try_acquire(
        tenant_id=principal.tenant_id,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        max_total=settings.sse_max_connections_total,
        max_workspace=settings.sse_max_connections_per_workspace,
        max_user_workspace=settings.sse_max_connections_per_user_workspace,
    )
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "stream_limit_reached",
                "message": "Stream connection capacity reached for this replica",
            },
            headers={"Retry-After": "5"},
        )

    released = False

    def release_slot() -> None:
        nonlocal released
        if not released:
            released = True
            cap_mgr.release(
                tenant_id=principal.tenant_id,
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
            )

    # 2. Run preflight before HTTP headers are committed
    try:
        async with asyncio.timeout(min(settings.sse_query_timeout_seconds, 1.0)):
            (
                initial_cursor,
                head_seq,
                _oldest_seq,
                initial_status,
                resolved_trace_id,
                initial_batch,
            ) = await service.get_stream_preflight(
                principal=principal,
                run_id=run_id,
                query_cursor=last_event_id,
                header_cursor=last_event_id_header,
            )
    except RunEventError as exc:
        release_slot()
        raise _translate_run_event_error(exc) from exc
    except TimeoutError as exc:
        release_slot()
        raise _translate_run_event_error(RunEventUnavailableError()) from exc
    except BaseException:
        release_slot()
        raise

    max_auth_age = min(settings.sse_max_auth_age_seconds, 5.0)
    lookup_timeout = min(settings.sse_auth_lookup_timeout_seconds, max_auth_age * 0.5, 1.5)
    poll_interval = min(settings.sse_poll_interval_seconds, 1.0)
    max_duration = settings.sse_max_stream_duration_seconds
    heartbeat_interval = settings.sse_heartbeat_interval_seconds

    async def event_generator() -> AsyncGenerator[str, None]:
        current_principal = principal
        last_verified_at = time.monotonic()
        last_heartbeat_at = time.monotonic()
        stream_started_at = time.monotonic()
        cursor_seq = initial_cursor
        pending_error: list[str] = []

        async def _verify_fresh_auth() -> bool:
            nonlocal current_principal, last_verified_at
            elapsed = time.monotonic() - last_verified_at
            remaining_lease = max(0.05, max_auth_age - elapsed)
            effective_timeout = min(lookup_timeout, remaining_lease)
            try:
                async with asyncio.timeout(effective_timeout):
                    current_principal = await revalidate_runtime_authorization(
                        current_principal,
                        required_scope="run:read",
                        session_factory=scoped_session,
                        settings=settings,
                        timeout_seconds=effective_timeout,
                    )
                    await service.verify_stream_resource_authority(
                        principal=current_principal, run_id=run_id
                    )
                    if (
                        current_principal.token_expires_at is not None
                        and time.time() >= current_principal.token_expires_at
                    ):
                        raise RuntimeStreamAuthError(
                            "authorization_expired", "Runtime token expired"
                        )
                last_verified_at = time.monotonic()
                return True
            except RuntimeStreamAuthError as auth_err:
                code = (
                    "backend_unavailable"
                    if auth_err.code == "authorization_unavailable"
                    else auth_err.code
                )
                pending_error.append(_format_sse_error(code, auth_err.message))
                return False
            except (RunEventAccessDeniedError, RunEventNotFoundError):
                pending_error.append(
                    _format_sse_error("authorization_revoked", "Run authorization revoked")
                )
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Unexpected error during stream revalidation (%s)", type(exc).__name__
                )
                pending_error.append(
                    _format_sse_error("backend_unavailable", "Authorization unavailable")
                )
                return False

        try:
            yield f": connected to run {run_id}\n\n"

            # 3. Drain preflight initial batch
            for event in initial_batch:
                if not await _verify_fresh_auth():
                    yield pending_error.pop(0)
                    return
                cursor_seq = event.sequence
                yield f"id: {event.sequence}\nevent: {event.type}\ndata: {event.to_compact_json()}\n\n"

            current_status = initial_status
            if current_status in TERMINAL_RUN_STATUSES and cursor_seq >= head_seq:
                return

            # 4. Bounded polling loop
            while True:
                # 15-minute stream cap: close normally for client to reconnect
                if (time.monotonic() - stream_started_at) >= max_duration:
                    return

                # Idle auth supervision (check at least every 2 seconds when idle)
                if (time.monotonic() - last_verified_at) >= min(
                    settings.sse_revalidation_interval_seconds, 2.0
                ) and not await _verify_fresh_auth():
                    yield pending_error.pop(0)
                    return

                # Fetch next events in short closed transaction
                try:
                    async with asyncio.timeout(min(settings.sse_query_timeout_seconds, 1.0)):
                        current_status, next_events = await service.fetch_next_events(
                            principal=current_principal,
                            run_id=run_id,
                            cursor_seq=cursor_seq,
                            limit=50,
                            default_trace_id=resolved_trace_id,
                        )
                except TimeoutError:
                    yield _format_sse_error("backend_unavailable", "Database query timed out")
                    return
                except RunEventFrameTooLargeError:
                    yield _format_sse_error("frame_too_large", "Event envelope exceeds 16384 bytes")
                    return
                except RunEventHistoryInconsistentError as err:
                    yield _format_sse_error(err.code, err.message)
                    return
                except RunEventError as err:
                    if isinstance(err, (RunEventAccessDeniedError, RunEventNotFoundError)):
                        yield _format_sse_error(
                            "authorization_revoked", "Run authorization revoked"
                        )
                    else:
                        code = (
                            "event_history_inconsistent"
                            if isinstance(
                                err, (RunEventCursorAheadError, RunEventHistoryPrunedError)
                            )
                            else err.code
                        )
                        yield _format_sse_error(code, "Run event service unavailable")
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error fetching stream events (%s)", type(exc).__name__)
                    yield _format_sse_error("backend_unavailable", "Failed to fetch events")
                    return

                # Emit business frames
                for event in next_events:
                    if not await _verify_fresh_auth():
                        yield pending_error.pop(0)
                        return
                    cursor_seq = event.sequence
                    yield f"id: {event.sequence}\nevent: {event.type}\ndata: {event.to_compact_json()}\n\n"

                # Check terminal drain
                if not next_events and current_status in TERMINAL_RUN_STATUSES:
                    return

                if not next_events:
                    # Heartbeat comment if idle
                    now = time.monotonic()
                    if (now - last_heartbeat_at) >= heartbeat_interval:
                        yield ": keep-alive\n\n"
                        last_heartbeat_at = now

                    sleep_budget = min(
                        poll_interval,
                        max(
                            0.05,
                            max_auth_age - (time.monotonic() - last_verified_at) - lookup_timeout,
                        ),
                    )
                    await asyncio.sleep(sleep_budget)
                else:
                    last_heartbeat_at = time.monotonic()
                    await asyncio.sleep(0)

        finally:
            release_slot()

    return BoundedEventResponse(
        event_generator(),
        send_timeout=min(settings.sse_send_timeout_seconds, 2.0),
        max_duration=min(settings.sse_max_stream_duration_seconds, 900.0),
        on_close=release_slot,
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
