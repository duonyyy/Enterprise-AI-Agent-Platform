from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from agent_runtime.auth import CurrentRuntimePrincipal
from agent_runtime.persistence.database import scoped_session

router = APIRouter(tags=["runs"])


class StartRunRequest(BaseModel):
    workspace_id: str
    agent_version_id: str
    inputs: dict = Field(default_factory=dict)
    conversation_id: str | None = None


@router.post("/v1/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    req: StartRunRequest,
    principal: CurrentRuntimePrincipal,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        req_workspace_uuid = str(UUID(req.workspace_id))
        agent_version_uuid = str(UUID(req.agent_version_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace_id or agent_version_id UUID format",
        ) from exc

    if req_workspace_uuid != principal.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace scope denied: principal does not belong to requested workspace",
        )

    principal.require_scope("run:start")

    if idempotency_key is None or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key is required and must be at most 200 characters",
        )

    request_digest = hashlib.sha256(
        json.dumps(
            {
                "agent_version_id": agent_version_uuid,
                "conversation_id": req.conversation_id,
                "inputs": req.inputs,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    run_id = str(uuid4())

    async with scoped_session(
        principal.tenant_id,
        principal.workspace_id,
        principal.user_id,
    ) as session:
        # Create run in runtime.runs
        inserted = await session.execute(
            text("""
                INSERT INTO runtime.runs (
                    id, tenant_id, workspace_id, agent_version_id, initiated_by, external_request_key,
                    status, next_event_seq, created_at
                ) VALUES (
                    CAST(:run_id AS uuid),
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:agent_version_id AS uuid),
                    CAST(:user_id AS uuid),
                    :idempotency_key,
                    'queued',
                    2,
                    now()
                )
                ON CONFLICT (tenant_id, workspace_id, external_request_key) DO NOTHING
                RETURNING id
            """),
            {
                "run_id": run_id,
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "agent_version_id": agent_version_uuid,
                "user_id": principal.user_id,
                "idempotency_key": idempotency_key.strip(),
            },
        )
        created_run_id = inserted.scalar_one_or_none()
        if created_run_id is None:
            existing = await session.execute(
                text("""
                    SELECT r.id, r.status, e.redacted_payload
                    FROM runtime.runs AS r
                    LEFT JOIN runtime.run_events AS e
                      ON e.run_id = r.id AND e.tenant_id = r.tenant_id AND e.workspace_id = r.workspace_id
                     AND e.sequence = 1
                    WHERE r.tenant_id = CAST(:tenant_id AS uuid)
                      AND r.workspace_id = CAST(:workspace_id AS uuid)
                      AND r.external_request_key = :idempotency_key
                    FOR UPDATE
                """),
                {
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "idempotency_key": idempotency_key.strip(),
                },
            )
            row = existing.mappings().one()
            recorded_digest = (row["redacted_payload"] or {}).get("request_digest")
            if recorded_digest != request_digest:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key was previously used with a different request",
                )
            return {"run_id": str(row["id"]), "status": row["status"], "replayed": True}

        # Enqueue execution job in runtime.jobs
        await session.execute(
            text("""
                INSERT INTO runtime.jobs (
                    tenant_id, workspace_id, run_id, job_type, status, created_at
                ) VALUES (
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:run_id AS uuid),
                    'run',
                    'queued',
                    now()
                )
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_id,
            },
        )

        # Record initial event in runtime.run_events
        await session.execute(
            text("""
                INSERT INTO runtime.run_events (
                    tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload
                ) VALUES (
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:run_id AS uuid),
                    1,
                    'run.created',
                    CAST(:payload AS jsonb)
                )
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_id,
                "payload": json.dumps({"request_digest": request_digest}),
            },
        )

    return {"run_id": run_id, "status": "queued", "replayed": False}


@router.get("/v1/runs/{run_id}")
async def get_run(
    run_id: str,
    principal: CurrentRuntimePrincipal,
) -> dict:
    principal.require_scope("run:read")
    try:
        run_uuid = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run_id UUID format",
        ) from exc

    async with scoped_session(
        principal.tenant_id,
        principal.workspace_id,
        principal.user_id,
    ) as session:
        result = await session.execute(
            text("""
                SELECT id, agent_version_id, conversation_id, workflow_version_id,
                       initiated_by, status, trace_id, next_event_seq, started_at, finished_at, created_at
                FROM runtime.runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid)
                  AND id = CAST(:run_id AS uuid)
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_uuid,
            },
        )
        record = result.mappings().one_or_none()
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run '{run_id}' not found",
            )

        return {
            "run_id": str(record["id"]),
            "agent_version_id": str(record["agent_version_id"]),
            "conversation_id": str(record["conversation_id"]) if record["conversation_id"] else None,
            "status": record["status"],
            "trace_id": record["trace_id"],
            "next_event_seq": record["next_event_seq"],
            "started_at": record["started_at"].isoformat() if record["started_at"] else None,
            "finished_at": record["finished_at"].isoformat() if record["finished_at"] else None,
            "created_at": record["created_at"].isoformat() if record["created_at"] else None,
        }


@router.post("/internal/v1/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    principal: CurrentRuntimePrincipal,
) -> dict:
    principal.require_scope("run:start")
    try:
        run_uuid = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run_id UUID format",
        ) from exc

    async with scoped_session(
        principal.tenant_id,
        principal.workspace_id,
        principal.user_id,
    ) as session:
        # Check current run status
        result = await session.execute(
            text("""
                SELECT status FROM runtime.runs
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid)
                  AND id = CAST(:run_id AS uuid)
                FOR UPDATE
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_uuid,
            },
        )
        record = result.mappings().one_or_none()
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run '{run_id}' not found",
            )

        if record["status"] != "waiting_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot resume run with status '{record['status']}'. Must be 'waiting_approval'.",
            )

        # Update run status to queued
        await session.execute(
            text("""
                UPDATE runtime.runs
                SET status = 'queued',
                    next_event_seq = next_event_seq + 1
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid)
                  AND id = CAST(:run_id AS uuid)
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_uuid,
            },
        )

        # Enqueue resume job
        await session.execute(
            text("""
                INSERT INTO runtime.jobs (
                    tenant_id, workspace_id, run_id, job_type, status, created_at
                ) VALUES (
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:run_id AS uuid),
                    'resume',
                    'queued',
                    now()
                )
            """),
            {
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
                "run_id": run_uuid,
            },
        )

    return {"run_id": run_id, "status": "queued"}
