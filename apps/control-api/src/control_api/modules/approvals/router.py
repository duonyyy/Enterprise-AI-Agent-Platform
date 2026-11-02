from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from control_api.core.dependencies import CurrentUser, DBSession
from control_api.modules.approvals.schemas import DecisionInputDTO
from platform_common.principal import AuthenticatedPrincipal

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/approvals", tags=["approvals"])


@router.get("")
async def list_pending_approvals(
    workspace_id: str,
    principal: CurrentUser,
    session: DBSession,
) -> list[dict]:
    query = text("""
        SELECT id, run_id, step_key, action_hash, status, approver_role, revision, expires_at, created_at
        FROM runtime.approval_requests
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND workspace_id = CAST(:workspace_id AS uuid)
          AND status = 'pending'
          AND expires_at > now()
        ORDER BY created_at ASC
    """)
    result = await session.execute(
        query,
        {"tenant_id": principal.tenant_id, "workspace_id": principal.workspace_id},
    )
    rows = result.mappings().all()
    return [
        {
            "id": str(r["id"]),
            "run_id": str(r["run_id"]),
            "step_key": r["step_key"],
            "action_hash": r["action_hash"],
            "status": r["status"],
            "approver_role": r["approver_role"],
            "revision": r["revision"],
            "expires_at": r["expires_at"].isoformat() if hasattr(r["expires_at"], "isoformat") else str(r["expires_at"]),
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
        }
        for r in rows
    ]


@router.post("/{approval_id}/decision")
async def commit_decision(
    workspace_id: str,
    approval_id: str,
    dto: DecisionInputDTO,
    principal: CurrentUser,
    session: DBSession,
) -> dict:
    try:
        approval_uuid = str(UUID(approval_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid approval_id UUID format",
        ) from exc

    # CAS fetch with row-level lock
    query = text("""
        SELECT id, run_id, step_key, action_hash, status, approver_role, revision, expires_at
        FROM runtime.approval_requests
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND workspace_id = CAST(:workspace_id AS uuid)
          AND id = CAST(:approval_id AS uuid)
        FOR UPDATE
    """)
    result = await session.execute(
        query,
        {
            "tenant_id": principal.tenant_id,
            "workspace_id": principal.workspace_id,
            "approval_id": approval_uuid,
        },
    )
    record = result.mappings().one_or_none()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{approval_id}' not found",
        )

    # Validate pending status
    if record["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval request '{approval_id}' is already finalized as '{record['status']}'",
        )

    # Validate expiration
    now_dt = datetime.now(timezone.utc)
    if record["expires_at"] <= now_dt:
        await session.execute(
            text("""
                UPDATE runtime.approval_requests
                SET status = 'expired'
                WHERE id = CAST(:approval_id AS uuid)
                  AND tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid)
            """),
            {
                "approval_id": approval_uuid,
                "tenant_id": principal.tenant_id,
                "workspace_id": principal.workspace_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Approval request '{approval_id}' has expired",
        )

    # Validate action_hash tamper protection
    if record["action_hash"].strip() != dto.expected_action_hash.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action hash mismatch. The proposed arguments have been modified.",
        )

    # Validate approver role
    required_role = record["approver_role"]
    user_roles = set(principal.roles)
    if required_role and required_role not in user_roles and "owner" not in user_roles and "admin" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Principal requires '{required_role}' or admin role to decide this approval",
        )

    decision_lower = dto.decision.lower()

    # CAS update on approval_requests
    await session.execute(
        text("""
            UPDATE runtime.approval_requests
            SET status = :decision,
                decided_by = CAST(:actor_id AS uuid),
                decided_at = now(),
                revision = revision + 1
            WHERE id = CAST(:approval_id AS uuid)
              AND tenant_id = CAST(:tenant_id AS uuid)
              AND workspace_id = CAST(:workspace_id AS uuid)
              AND status = 'pending'
        """),
        {
            "decision": decision_lower,
            "actor_id": principal.user_id,
            "approval_id": approval_uuid,
            "tenant_id": principal.tenant_id,
            "workspace_id": principal.workspace_id,
        },
    )

    # Insert immutable audit record into approval_decisions
    await session.execute(
        text("""
            INSERT INTO runtime.approval_decisions (
                tenant_id, workspace_id, approval_request_id, actor_id, decision, decision_note_ref, decided_at
            ) VALUES (
                CAST(:tenant_id AS uuid),
                CAST(:workspace_id AS uuid),
                CAST(:approval_id AS uuid),
                CAST(:actor_id AS uuid),
                :decision,
                :decision_reason,
                now()
            )
        """),
        {
            "tenant_id": principal.tenant_id,
            "workspace_id": principal.workspace_id,
            "approval_id": approval_uuid,
            "actor_id": principal.user_id,
            "decision": decision_lower,
            "decision_reason": dto.decision_reason,
        },
    )

    return {
        "approval_id": approval_uuid,
        "status": dto.decision,
        "decided_by": principal.user_id,
        "action_hash": record["action_hash"].strip(),
        "revision": record["revision"] + 1,
    }
