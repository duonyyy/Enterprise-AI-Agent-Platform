from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from agent_runtime.auth import CurrentRuntimePrincipal
from agent_runtime.persistence.database import scoped_session

router = APIRouter(tags=["events"])


@router.get("/v1/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    principal: CurrentRuntimePrincipal,
    last_event_id: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        run_uuid = str(UUID(run_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run_id UUID format",
        ) from exc

    async def event_generator():
        # First verify the run exists and belongs to the principal
        async with scoped_session(principal.tenant_id, principal.workspace_id) as session:
            check_result = await session.execute(
                text("""
                    SELECT id FROM runtime.runs
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
            if not check_result.mappings().one_or_none():
                yield f"event: error\ndata: {json.dumps({'error': 'Run not found'})}\n\n"
                return

            # Yield connected handshake
            yield f": connected to run {run_id}\n\n"

            # Query existing events starting after last_event_id
            events_result = await session.execute(
                text("""
                    SELECT sequence, event_type, redacted_payload, created_at
                    FROM runtime.run_events
                    WHERE tenant_id = CAST(:tenant_id AS uuid)
                      AND workspace_id = CAST(:workspace_id AS uuid)
                      AND run_id = CAST(:run_id AS uuid)
                      AND sequence > :last_event_id
                    ORDER BY sequence ASC
                """),
                {
                    "tenant_id": principal.tenant_id,
                    "workspace_id": principal.workspace_id,
                    "run_id": run_uuid,
                    "last_event_id": last_event_id,
                },
            )
            for event in events_result.mappings().all():
                payload = event["redacted_payload"]
                if isinstance(payload, str):
                    payload_str = payload
                else:
                    payload_str = json.dumps(payload)
                yield f"id: {event['sequence']}\nevent: {event['event_type']}\ndata: {payload_str}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
