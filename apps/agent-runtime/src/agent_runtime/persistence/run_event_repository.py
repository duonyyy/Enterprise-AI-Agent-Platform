"""PostgreSQL persistence repository for durable run events, locking, and projection (06-C)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_events import (
    ALLOWED_EVENT_TYPES,
    MAX_SAFE_INTEGER,
    RunEventDTO,
    RunEventNotFoundError,
    RunEventValidationError,
    map_row_to_run_event_dto,
    project_event_data,
)


def _metadata_sql(name: str, legacy: str, kind: str) -> str:
    """Build expressions from fixed internal field names, never request input.

    CASE bounds optional values in PostgreSQL before they cross the driver.
    """
    value = f"COALESCE(redacted_payload->'{name}', redacted_payload->'{legacy}')"
    scalar = f"({value} #>> '{{}}')"
    if kind == "uuid":
        valid = f"{scalar} ~ '^[0-9a-fA-F]{{8}}(-[0-9a-fA-F]{{4}}){{3}}-[0-9a-fA-F]{{12}}$'"
        return f"CASE WHEN jsonb_typeof({value})='string' AND octet_length({scalar})=36 THEN CASE WHEN {valid} THEN {value} END END"
    if kind == "boolean":
        return f"CASE WHEN jsonb_typeof({value})='boolean' THEN {value} END"
    if kind == "attempt":
        return f"CASE WHEN jsonb_typeof({value})='number' AND {scalar} ~ '^[1-9][0-9]{{0,15}}$' THEN CASE WHEN {scalar}::numeric <= {MAX_SAFE_INTEGER} THEN {value} END END"
    raise ValueError("Unsupported internal metadata kind")


def _projection_sql() -> str:
    variants: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
        "run.queued": (
            "queued",
            [
                ("agentVersionId", "agent_version_id", "uuid"),
                ("executionAvailable", "execution_available", "boolean"),
            ],
        ),
        "run.started": ("running", []),
        "run.step_started": (
            "running",
            [("stepId", "step_id", "uuid"), ("attempt", "step_attempt", "attempt")],
        ),
        "run.tool_call_requested": (
            "requested",
            [
                ("toolOperationId", "tool_operation_id", "uuid"),
                ("toolVersionId", "tool_version_id", "uuid"),
            ],
        ),
        "run.tool_call_completed": (
            "completed",
            [
                ("toolOperationId", "tool_operation_id", "uuid"),
                ("toolVersionId", "tool_version_id", "uuid"),
            ],
        ),
        "run.approval_requested": ("waiting_approval", [("approvalId", "approval_id", "uuid")]),
        "run.approval_resolved": ("resolved", [("approvalId", "approval_id", "uuid")]),
        "run.completed": ("succeeded", []),
        "run.failed": ("failed", []),
        "run.cancelled": ("cancelled", []),
        "run.timed_out": ("timed_out", []),
    }
    clauses = []
    for kind, (status, fields) in variants.items():
        pairs = [f"'status', '{status}'"]
        pairs.extend(
            f"'{name}', {_metadata_sql(name, legacy, field_kind)}"
            for name, legacy, field_kind in fields
        )
        if kind == "run.queued":
            pairs.append("""'availabilityReason', CASE WHEN
                COALESCE(redacted_payload->'executionAvailable', redacted_payload->'execution_available') = 'false'::jsonb
                AND COALESCE(redacted_payload->'availabilityReason', redacted_payload->'availability_reason') = '"no_executor_configured"'::jsonb
                THEN 'no_executor_configured' END""")
        condition = (
            "event_type IN ('run.queued', 'run.created')"
            if kind == "run.queued"
            else f"event_type='{kind}'"
        )
        clauses.append(f"WHEN {condition} THEN jsonb_build_object({', '.join(pairs)})")
    return (
        "jsonb_strip_nulls(CASE "
        + " ".join(clauses)
        + " ELSE jsonb_build_object('__unknown__', true) END)"
    )


_EVENT_DATA_SQL = _projection_sql()


class RunEventRepository:
    """Caller-session-scoped repository for atomic run event append and projected keyset reads."""

    async def get_run_authority(
        self, session: AsyncSession, *, tenant_id: str, workspace_id: str, run_id: str
    ) -> dict[str, Any] | None:
        """Resource authorization must not depend on timeline parsing or its health."""
        row = (
            (
                await session.execute(
                    text("""
            SELECT initiated_by FROM runtime.runs
            WHERE tenant_id=:tenant AND workspace_id=:workspace AND id=:run
        """),
                    {
                        "tenant": UUID(tenant_id),
                        "workspace": UUID(workspace_id),
                        "run": UUID(run_id),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return {
            "initiated_by": str(row["initiated_by"]) if row["initiated_by"] is not None else None
        }

    async def append_run_event(
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
        """Atomically lock run row, allocate monotonic next_event_seq, and insert validated event."""
        if event_type not in ALLOWED_EVENT_TYPES:
            raise RunEventValidationError(
                f"Unsupported event type: '{event_type}'",
                code="invalid_event_type",
            )

        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        r_uuid = UUID(run_id)

        # 1. Lock run row FOR UPDATE to linearize sequence allocation within caller transaction.
        run_row = (
            (
                await session.execute(
                    text("""
                        SELECT next_event_seq, status, trace_id
                        FROM runtime.runs
                        WHERE tenant_id = :tenant_id
                          AND workspace_id = :workspace_id
                          AND id = :run_id
                        FOR UPDATE
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "run_id": r_uuid,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            raise RunEventNotFoundError(f"Run '{run_id}' not found", code="run_not_found")

        allocated_seq = int(run_row["next_event_seq"])
        if allocated_seq > MAX_SAFE_INTEGER:
            raise RunEventValidationError(
                f"Sequence number exceeds max safe integer ({MAX_SAFE_INTEGER})",
                code="sequence_overflow",
            )

        # 2. Strict allowlist projection before database write
        projected_payload = project_event_data(event_type, data)
        event_id = str(uuid4())
        resolved_trace = trace_id or run_row["trace_id"]

        # 3. Insert into runtime.run_events
        insert_result = await session.execute(
            text("""
                INSERT INTO runtime.run_events (
                    id, tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload, created_at
                ) VALUES (
                    :event_id, :tenant_id, :workspace_id, :run_id, :sequence, :event_type,
                    CAST(:redacted_payload AS jsonb), clock_timestamp()
                )
                RETURNING created_at
            """),
            {
                "event_id": UUID(event_id),
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "run_id": r_uuid,
                "sequence": allocated_seq,
                "event_type": event_type,
                "redacted_payload": json.dumps(
                    projected_payload, sort_keys=True, separators=(",", ":")
                ),
            },
        )

        # 4. Increment next_event_seq
        await session.execute(
            text("""
                UPDATE runtime.runs
                SET next_event_seq = next_event_seq + 1
                WHERE tenant_id = :tenant_id
                  AND workspace_id = :workspace_id
                  AND id = :run_id
            """),
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "run_id": r_uuid,
            },
        )

        row_dict = {
            "id": event_id,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "sequence": allocated_seq,
            "event_type": event_type,
            "created_at": insert_result.scalar_one(),
            "projected_data": projected_payload,
            "trace_id": resolved_trace,
        }
        return map_row_to_run_event_dto(
            row_dict,
            workspace_id=workspace_id,
            run_id=run_id,
            default_trace_id=resolved_trace,
        )

    async def get_run_snapshot(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Fetch head sequence, oldest available sequence, status, and authority in caller transaction."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        r_uuid = UUID(run_id)

        run_row = (
            (
                await session.execute(
                    text("""
                        SELECT r.next_event_seq, r.status, r.initiated_by, r.trace_id,
                               (SELECT MIN(e.sequence) FROM runtime.run_events e
                                WHERE e.tenant_id=r.tenant_id AND e.workspace_id=r.workspace_id
                                  AND e.run_id=r.id) AS oldest_sequence,
                               (SELECT MAX(e.sequence) FROM runtime.run_events e
                                WHERE e.tenant_id=r.tenant_id AND e.workspace_id=r.workspace_id
                                  AND e.run_id=r.id) AS tail_sequence
                        FROM runtime.runs r
                        WHERE r.tenant_id = :tenant_id
                          AND r.workspace_id = :workspace_id
                          AND r.id = :run_id
                    """),
                    {
                        "tenant_id": t_uuid,
                        "workspace_id": w_uuid,
                        "run_id": r_uuid,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if run_row is None:
            return None

        oldest_seq_val = run_row["oldest_sequence"]

        next_seq = run_row["next_event_seq"]
        head = next_seq - 1
        if oldest_seq_val is not None:
            oldest = int(oldest_seq_val)
        else:
            oldest = head + 1 if head > 0 else 1
        prefix_floor = oldest - 1

        return {
            "head": head,
            "oldest": oldest,
            "prefix_floor": prefix_floor,
            "status": str(run_row.get("status", "running")),
            "initiated_by": str(run_row["initiated_by"]) if run_row.get("initiated_by") else None,
            "trace_id": run_row.get("trace_id"),
            "tail_consistent": oldest_seq_val is None or run_row["tail_sequence"] == head,
        }

    async def fetch_events_batch(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        cursor_seq: int,
        limit: int,
        head_sequence: int = MAX_SAFE_INTEGER,
    ) -> list[dict[str, Any]]:
        """Project allowlisted metadata at SQL level, preventing oversized/unauthorized JSONB driver transfer."""
        t_uuid = UUID(tenant_id)
        w_uuid = UUID(workspace_id)
        r_uuid = UUID(run_id)

        stmt = text(f"""
            SELECT id, sequence, event_type, created_at,
                   {_EVENT_DATA_SQL} AS projected_data
            FROM runtime.run_events
            WHERE tenant_id = :tenant_id
              AND workspace_id = :workspace_id
              AND run_id = :run_id
              AND sequence > :cursor_seq AND sequence <= :head_sequence
            ORDER BY sequence ASC LIMIT :limit
        """)

        result = await session.execute(
            stmt,
            {
                "tenant_id": t_uuid,
                "workspace_id": w_uuid,
                "run_id": r_uuid,
                "cursor_seq": cursor_seq,
                "head_sequence": head_sequence,
                "limit": limit,
            },
        )
        return [dict(r) for r in result.mappings().all()]
