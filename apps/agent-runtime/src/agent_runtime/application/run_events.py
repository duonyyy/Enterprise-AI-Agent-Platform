"""Run event DTOs, canonical JSON schema mapping, projection allowlists, and cursor validation (06-C)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_FRAME_BYTES: int = 16384
MAX_SAFE_INTEGER: int = 9007199254740991
DEFAULT_HISTORY_LIMIT: int = 50
MAX_HISTORY_LIMIT: int = 100
SCHEMA_VERSION_1_2: str = "1.2"

_CURSOR_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class RunEventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_STEP_STARTED = "run.step_started"
    RUN_TOOL_CALL_REQUESTED = "run.tool_call_requested"
    RUN_TOOL_CALL_COMPLETED = "run.tool_call_completed"
    RUN_APPROVAL_REQUESTED = "run.approval_requested"
    RUN_APPROVAL_RESOLVED = "run.approval_resolved"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"


ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(e.value for e in RunEventType)
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "timed_out"})


class RunEventError(RuntimeError):
    """Base exception for run events and SSE streaming."""

    def __init__(self, message: str, *, code: str = "run_event_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunEventValidationError(RunEventError):
    """Raised on invalid cursor, limit, UUID, or malformed request parameters."""

    def __init__(self, message: str, *, code: str = "invalid_cursor") -> None:
        super().__init__(message, code=code)


class RunEventAccessDeniedError(RunEventError):
    """Raised on scope or membership access denial."""

    def __init__(self, message: str, *, code: str = "access_denied") -> None:
        super().__init__(message, code=code)


class RunEventNotFoundError(RunEventError):
    """Raised when run does not exist or is a hidden peer run."""

    def __init__(self, message: str = "Run not found", *, code: str = "run_not_found") -> None:
        super().__init__(message, code=code)


class RunEventCursorAheadError(RunEventError):
    """Raised (409) when cursor exceeds committed head sequence."""

    def __init__(
        self,
        message: str = "Cursor exceeds committed head sequence",
        *,
        code: str = "cursor_ahead",
    ) -> None:
        super().__init__(message, code=code)


class RunEventHistoryPrunedError(RunEventError):
    """Raised (410) when cursor precedes oldest retained sequence."""

    def __init__(
        self,
        message: str = "Event history has been pruned before requested cursor",
        *,
        oldest_available_sequence: int,
        head_sequence: int,
        code: str = "history_pruned",
    ) -> None:
        super().__init__(message, code=code)
        self.oldest_available_sequence = oldest_available_sequence
        self.head_sequence = head_sequence


class RunEventStreamLimitReachedError(RunEventError):
    """Raised (429) when replica connection capacity is reached."""

    def __init__(
        self,
        message: str = "Stream connection capacity reached",
        *,
        code: str = "stream_limit_reached",
        retry_after: int = 5,
    ) -> None:
        super().__init__(message, code=code)
        self.retry_after = retry_after


class RunEventHistoryInconsistentError(RunEventError):
    """Raised (503) when internal gaps, corrupt rows, or missing sequences are detected."""

    def __init__(
        self,
        message: str = "Event history sequence is inconsistent",
        *,
        code: str = "event_history_inconsistent",
    ) -> None:
        super().__init__(message, code=code)


class RunEventFrameTooLargeError(RunEventError):
    """Raised when serialized event envelope exceeds frame byte cap (16384 bytes)."""

    def __init__(
        self,
        message: str = "Event frame exceeds maximum allowed size",
        *,
        code: str = "frame_too_large",
    ) -> None:
        super().__init__(message, code=code)


class RunEventUnavailableError(RunEventError):
    """Raised (503) on backend/database timeout or unavailability."""

    def __init__(
        self,
        message: str = "Event service temporarily unavailable",
        *,
        code: str = "backend_unavailable",
    ) -> None:
        super().__init__(message, code=code)


class RunEventDTO(BaseModel):
    """Strict canonical RunEvent v1.2 envelope DTO (additionalProperties=false)."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: str = Field(default=SCHEMA_VERSION_1_2)
    eventId: str
    runId: str
    workspaceId: str
    sequence: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    type: str
    timestamp: str
    traceId: str | None = None
    data: dict[str, Any]

    def to_compact_json(self) -> str:
        """Serialize compact JSON representation without whitespace for SSE wire delivery."""
        payload = self.model_dump(exclude_none=True)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    def to_wire_envelope(self) -> dict[str, Any]:
        """Serialize event as dictionary matching wire contract envelope."""
        return self.model_dump(exclude_none=True)


class RunHistoryResponseDTO(BaseModel):
    """Strict response DTO for GET /v1/runs/{run_id}/history."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    events: list[RunEventDTO]
    next_cursor: str
    has_more: bool
    oldest_available_sequence: int
    head_sequence: int


def parse_and_validate_cursor(raw_cursor: str | int | None) -> int:
    """Validate decimal canonical cursor (0 or [1-9][0-9]*, max 9007199254740991).

    Rejects floats, signs, scientific notation, leading zeroes, and whitespace.
    """
    if raw_cursor is None:
        return 0
    if isinstance(raw_cursor, bool):
        raise RunEventValidationError("Invalid cursor", code="invalid_cursor")
    if isinstance(raw_cursor, int):
        if raw_cursor < 0 or raw_cursor > MAX_SAFE_INTEGER:
            raise RunEventValidationError(
                f"Cursor must be between 0 and {MAX_SAFE_INTEGER}",
                code="invalid_cursor",
            )
        return raw_cursor

    if not isinstance(raw_cursor, str):
        raise RunEventValidationError("Cursor must be a decimal string", code="invalid_cursor")

    if len(raw_cursor) > 16 or not _CURSOR_RE.fullmatch(raw_cursor):
        raise RunEventValidationError(
            "Cursor must be a canonical decimal string without signs, leading zeroes, or whitespace",
            code="invalid_cursor",
        )
    val = int(raw_cursor)
    if val > MAX_SAFE_INTEGER:
        raise RunEventValidationError(
            f"Cursor exceeds maximum safe integer ({MAX_SAFE_INTEGER})",
            code="invalid_cursor",
        )
    return val


def resolve_sse_cursor(
    *,
    query_cursor: str | None,
    header_cursor: str | None,
) -> int:
    """Resolve cursor from query last_event_id and Last-Event-ID header.

    Rules:
      - Empty query string '' -> 400 (invalid_cursor)
      - Empty header '' -> treated as absent
      - Both present -> must match exactly, else 400 (cursor_conflict)
      - If neither present -> 0
    """
    if query_cursor == "":
        raise RunEventValidationError(
            "Query parameter last_event_id cannot be empty",
            code="invalid_cursor",
        )

    clean_header = header_cursor
    if clean_header == "":
        clean_header = None

    if query_cursor is not None and clean_header is not None:
        if query_cursor != clean_header:
            raise RunEventValidationError(
                "Conflicting query last_event_id and Last-Event-ID header",
                code="cursor_conflict",
            )
        return parse_and_validate_cursor(query_cursor)

    if query_cursor is not None:
        return parse_and_validate_cursor(query_cursor)

    if clean_header is not None:
        return parse_and_validate_cursor(clean_header)

    return 0


def format_iso8601_utc(dt: datetime | None) -> str:
    """Format datetime as RFC 3339 UTC with 'Z' suffix."""
    if dt is None:
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    # Format with microseconds trimmed or clean ISO
    return dt.isoformat().replace("+00:00", "Z")


def project_event_data(
    wire_type: str,
    raw_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Map and project data object according to per-type allowlist in D6-CD02.

    Derived status is inferred strictly from wire event type.
    Non-allowlisted keys (prompts, tool args, raw text, etc.) are discarded.
    """
    if raw_payload is not None and not isinstance(raw_payload, Mapping):
        raise RunEventHistoryInconsistentError("Invalid event data", code="event_invalid")
    raw = raw_payload or {}
    data: dict[str, Any] = {}

    match wire_type:
        case RunEventType.RUN_QUEUED:
            data["status"] = "queued"
            av_id = raw.get("agentVersionId") or raw.get("agent_version_id")
            if av_id is not None:
                try:
                    data["agentVersionId"] = str(UUID(str(av_id)))
                except (ValueError, TypeError):
                    pass
            exec_avail = raw.get("executionAvailable")
            if exec_avail is None:
                exec_avail = raw.get("execution_available")
            if isinstance(exec_avail, bool):
                data["executionAvailable"] = exec_avail
                reason = raw.get("availabilityReason", raw.get("availability_reason"))
                if not exec_avail and reason == "no_executor_configured":
                    data["availabilityReason"] = "no_executor_configured"

        case RunEventType.RUN_STARTED:
            data["status"] = "running"

        case RunEventType.RUN_STEP_STARTED:
            data["status"] = "running"
            step_id = raw.get("stepId") or raw.get("step_id")
            if step_id is not None:
                try:
                    data["stepId"] = str(UUID(str(step_id)))
                except (ValueError, TypeError):
                    pass
            attempt = raw.get("attempt") or raw.get("step_attempt")
            if type(attempt) is int and 1 <= attempt <= MAX_SAFE_INTEGER:
                data["attempt"] = attempt

        case RunEventType.RUN_TOOL_CALL_REQUESTED:
            data["status"] = "requested"
            op_id = raw.get("toolOperationId") or raw.get("tool_operation_id")
            tv_id = raw.get("toolVersionId") or raw.get("tool_version_id")
            if op_id is not None:
                try:
                    data["toolOperationId"] = str(UUID(str(op_id)))
                except (ValueError, TypeError):
                    pass
            if tv_id is not None:
                try:
                    data["toolVersionId"] = str(UUID(str(tv_id)))
                except (ValueError, TypeError):
                    pass

        case RunEventType.RUN_TOOL_CALL_COMPLETED:
            data["status"] = "completed"
            op_id = raw.get("toolOperationId") or raw.get("tool_operation_id")
            tv_id = raw.get("toolVersionId") or raw.get("tool_version_id")
            if op_id is not None:
                try:
                    data["toolOperationId"] = str(UUID(str(op_id)))
                except (ValueError, TypeError):
                    pass
            if tv_id is not None:
                try:
                    data["toolVersionId"] = str(UUID(str(tv_id)))
                except (ValueError, TypeError):
                    pass

        case RunEventType.RUN_APPROVAL_REQUESTED:
            data["status"] = "waiting_approval"
            appr_id = raw.get("approvalId") or raw.get("approval_id")
            if appr_id is not None:
                try:
                    data["approvalId"] = str(UUID(str(appr_id)))
                except (ValueError, TypeError):
                    pass

        case RunEventType.RUN_APPROVAL_RESOLVED:
            data["status"] = "resolved"
            appr_id = raw.get("approvalId") or raw.get("approval_id")
            if appr_id is not None:
                try:
                    data["approvalId"] = str(UUID(str(appr_id)))
                except (ValueError, TypeError):
                    pass

        case RunEventType.RUN_COMPLETED:
            data["status"] = "succeeded"

        case RunEventType.RUN_FAILED:
            data["status"] = "failed"
            data["errorCode"] = "run_failed"

        case RunEventType.RUN_CANCELLED:
            data["status"] = "cancelled"
            data["reasonCode"] = "user_cancelled"

        case RunEventType.RUN_TIMED_OUT:
            data["status"] = "timed_out"
            data["reasonCode"] = "queue_deadline_exceeded"

        case _:
            raise RunEventHistoryInconsistentError(
                f"Unknown event type '{wire_type}' cannot be projected",
                code="event_invalid",
            )

    return data


def map_row_to_run_event_dto(
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    run_id: str,
    default_trace_id: str | None = None,
) -> RunEventDTO:
    """Project database row to wire RunEvent 1.2 envelope DTO with strict allowlist and cap checks."""
    raw_id = row.get("id") or row.get("event_id") or row.get("eventId")
    try:
        event_id = str(UUID(str(raw_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RunEventHistoryInconsistentError(
            "Invalid event identity", code="event_invalid"
        ) from exc

    raw_type = str(row["event_type"])

    # Legacy translation: run.created -> run.queued
    wire_type = RunEventType.RUN_QUEUED if raw_type == "run.created" else raw_type
    if wire_type not in ALLOWED_EVENT_TYPES:
        raise RunEventHistoryInconsistentError(
            f"Encountered unsupported event type: '{raw_type}'",
            code="event_invalid",
        )

    seq = row["sequence"]
    if type(seq) is not int or seq < 1 or seq > MAX_SAFE_INTEGER:
        raise RunEventHistoryInconsistentError(
            f"Invalid sequence number {seq} in event row {event_id}",
            code="event_invalid",
        )

    # Resolve timestamp
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        parsed_time = created_at
    elif isinstance(created_at, str):
        try:
            parsed_time = datetime.fromisoformat(created_at)
        except ValueError as exc:
            raise RunEventHistoryInconsistentError(
                "Invalid event timestamp", code="event_invalid"
            ) from exc
    else:
        raise RunEventHistoryInconsistentError("Missing event timestamp", code="event_invalid")
    if parsed_time.tzinfo is None:
        raise RunEventHistoryInconsistentError("Invalid event timezone", code="event_invalid")
    ts_str = format_iso8601_utc(parsed_time)

    # Resolve traceId (only keep if trusted 32 hex chars)
    raw_trace = row.get("trace_id") or default_trace_id
    trusted_trace_id: str | None = None
    if raw_trace and isinstance(raw_trace, str) and _TRACE_ID_RE.fullmatch(raw_trace):
        trusted_trace_id = raw_trace

    # Payload projection
    projected = row.get("projected_data")
    if projected is not None and isinstance(projected, dict):
        if projected.get("__unknown__"):
            raise RunEventHistoryInconsistentError(
                f"Projected unknown event type for row {event_id}",
                code="event_invalid",
            )
        data = project_event_data(wire_type, projected)
    else:
        raw_payload = row.get("redacted_payload")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except Exception as exc:
                raise RunEventHistoryInconsistentError(
                    f"Corrupt JSON payload in event row {event_id}",
                    code="event_invalid",
                ) from exc
        data = project_event_data(wire_type, raw_payload)

    dto = RunEventDTO(
        schemaVersion=SCHEMA_VERSION_1_2,
        eventId=event_id,
        runId=str(UUID(run_id)),
        workspaceId=str(UUID(workspace_id)),
        sequence=seq,
        type=wire_type,
        timestamp=ts_str,
        traceId=trusted_trace_id,
        data=data,
    )

    # Verify frame cap (16384 bytes)
    wire_json = dto.to_compact_json()
    frame = f"id: {seq}\nevent: {wire_type}\ndata: {wire_json}\n\n"
    if len(frame.encode("utf-8")) > MAX_FRAME_BYTES:
        raise RunEventFrameTooLargeError(
            f"Event {event_id} envelope exceeds frame cap ({MAX_FRAME_BYTES} bytes)",
            code="frame_too_large",
        )

    return dto
