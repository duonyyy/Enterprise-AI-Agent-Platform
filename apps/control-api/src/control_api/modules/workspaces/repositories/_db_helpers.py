"""Internal persistence, error-mapping, and cursor helpers for workspace repositories."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID

from control_api.modules.workspaces.models.errors import (
    WorkspaceDatabaseUnavailableError,
    WorkspaceError,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_WORKSPACE_LOCK_CLAUSES: Final[dict[str, str]] = {
    "none": "",
    "share": " FOR SHARE OF w",
    "update": " FOR UPDATE OF w",
}


async def is_active_workspace_in_tenant(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    lock_mode: Literal["none", "share", "update"] = "none",
) -> bool:
    """Verify target workspace and parent tenant are both active, with optional row lock."""
    lock_clause = _WORKSPACE_LOCK_CLAUSES[lock_mode]
    ws_stmt = text(
        "SELECT w.id, w.status AS workspace_status, t.status AS tenant_status "
        "FROM iam.workspaces AS w "
        "JOIN iam.tenants AS t ON t.id = w.tenant_id "
        f"WHERE w.tenant_id = :tenant_id AND w.id = :workspace_id{lock_clause}"
    )
    ws_row = (
        (
            await session.execute(
                ws_stmt,
                {"tenant_id": tenant_id, "workspace_id": workspace_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return (
        ws_row is not None
        and str(ws_row["workspace_status"]) == "active"
        and str(ws_row["tenant_status"]) == "active"
    )


def is_unique_violation(exc: IntegrityError) -> bool:
    """Return True when an IntegrityError originates from a PostgreSQL unique violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate == "23505":
        return True
    msg = str(orig or exc).lower()
    return "unique" in msg or "duplicate key" in msg


def db_unavailable(
    exc: Exception,
    *,
    operation: str,
    message: str,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> WorkspaceDatabaseUnavailableError:
    """Log persistence outage metadata without SQL/PII and raise sanitized error."""
    extra: dict[str, Any] = {
        "operation": operation,
        "error_type": type(exc).__name__,
    }
    if tenant_id is not None:
        extra["tenant_id"] = str(tenant_id)
    if workspace_id is not None:
        extra["workspace_id"] = str(workspace_id)
    logger.warning(
        "Workspace persistence operation failed (%s: %s)",
        operation,
        type(exc).__name__,
        extra=extra,
    )
    return WorkspaceDatabaseUnavailableError(message)


def format_timestamp(value: Any) -> str | None:
    """Serialize a timestamp or string into UTC ISO-8601 format."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def encode_keyset_cursor(ts_field: str, ts_value: datetime, entity_id: str) -> str:
    """Encode an opaque URL-safe `(timestamp, id)` keyset cursor."""
    payload = json.dumps(
        {ts_field: ts_value.astimezone(UTC).isoformat(), "id": entity_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_keyset_cursor(
    cursor: str,
    *,
    ts_field: str,
    error_factory: Callable[[str, str], WorkspaceError],
) -> tuple[datetime, UUID]:
    """Decode and validate an opaque `(timestamp, id)` keyset cursor."""
    if not cursor or len(cursor) > 256:
        raise error_factory("invalid_cursor", "Invalid pagination cursor")
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Cursor payload must be a JSON object")
        ts_value = datetime.fromisoformat(str(data[ts_field]))
        if ts_value.tzinfo is None:
            ts_value = ts_value.replace(tzinfo=UTC)
        entity_id = UUID(str(data["id"]))
        return ts_value, entity_id
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise error_factory("invalid_cursor", "Invalid pagination cursor") from exc
