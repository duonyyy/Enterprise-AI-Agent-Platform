"""Bounded read-only retention assessment. Counts never authorize physical deletion."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RunRetentionRepository:
    async def evaluate(
        self, session: AsyncSession, *, tenant_id: str, workspace_id: str, limit: int
    ) -> dict[str, Any]:
        params = {"tenant": UUID(tenant_id), "workspace": UUID(workspace_id), "limit": limit}
        policies = (
            (
                await session.execute(
                    text("""
            SELECT data_class, retention_days, legal_hold FROM governance.retention_policies
            WHERE tenant_id=:tenant AND workspace_id=:workspace
              AND data_class IN ('run_content','run_events','run_receipt')
        """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        policy = {str(p["data_class"]): p for p in policies}
        rows = (
            (
                await session.execute(
                    text("""
            SELECT r.status, r.finished_at, r.receipt_expires_at,
                (r.request_sha256 IS NULL OR r.input_ref IS NULL) AS unknown_association,
                c.status AS conversation_status,
                (SELECT max(cr.finished_at) FROM runtime.runs cr
                 WHERE cr.tenant_id=r.tenant_id AND cr.workspace_id=r.workspace_id
                   AND cr.conversation_id=r.conversation_id) AS conversation_finished_at,
                EXISTS(SELECT 1 FROM runtime.runs cr WHERE cr.tenant_id=r.tenant_id
                    AND cr.workspace_id=r.workspace_id AND cr.conversation_id=r.conversation_id
                    AND (cr.status IN ('queued','running','waiting_approval') OR cr.finished_at IS NULL)) AS conversation_active,
                EXISTS(SELECT 1 FROM runtime.runs sr WHERE sr.tenant_id=r.tenant_id
                    AND sr.workspace_id=r.workspace_id AND sr.id<>r.id AND sr.input_ref=r.input_ref) AS shared_run,
                EXISTS(SELECT 1 FROM runtime.messages m WHERE m.tenant_id=r.tenant_id
                    AND m.workspace_id=r.workspace_id AND m.body_ref=r.input_ref
                    AND m.conversation_id IS DISTINCT FROM r.conversation_id) AS shared_message,
                EXISTS(SELECT 1 FROM runtime.jobs j WHERE j.tenant_id=r.tenant_id
                    AND j.workspace_id=r.workspace_id AND (j.run_id=r.id OR j.payload_ref=r.input_ref)
                    AND j.status IN ('queued','leased')) AS active_job,
                EXISTS(SELECT 1 FROM runtime.checkpoint_refs cp WHERE cp.tenant_id=r.tenant_id
                    AND cp.workspace_id=r.workspace_id AND cp.run_id=r.id) AS checkpoint_reference
            FROM runtime.runs r LEFT JOIN runtime.conversations c
              ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id AND c.id=r.conversation_id
            WHERE r.tenant_id=:tenant AND r.workspace_id=:workspace
            ORDER BY r.finished_at NULLS LAST, r.id LIMIT :limit
        """),
                    params,
                )
            )
            .mappings()
            .all()
        )
        now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
        content = {
            "eligible": 0,
            "protected": 0,
            "legal_hold": 0,
            "active": 0,
            "shared": 0,
            "within_ttl": 0,
            "unknown": 0,
        }
        timeline = {"eligible": 0, "protected": 0}
        receipt = {"expired": 0, "protected": 0, "tombstones_retained": len(rows)}
        content_policy: dict[str, Any] = dict(policy.get("run_content", {}))
        timeline_policy: dict[str, Any] = dict(policy.get("run_events", {}))
        receipt_policy: dict[str, Any] = dict(policy.get("run_receipt", {}))
        content_days = int(content_policy.get("retention_days", 30))
        timeline_days = int(timeline_policy.get("retention_days", 90))
        for row in rows:
            finished: datetime | None = row["finished_at"]
            active = row["status"] in ("queued", "running", "waiting_approval") or finished is None
            held = bool(content_policy.get("legal_hold", False))
            shared = bool(
                row["shared_run"]
                or row["shared_message"]
                or row["active_job"]
                or row["checkpoint_reference"]
            )
            conversation_active = (
                row["conversation_status"] == "active" or row["conversation_active"]
            )
            anchor = row["conversation_finished_at"] or finished
            within_ttl = anchor is None or now < anchor + timedelta(days=content_days)
            unknown = bool(row["unknown_association"])
            protected = active or held or shared or conversation_active or within_ttl or unknown
            content["protected" if protected else "eligible"] += 1
            content["legal_hold"] += int(held)
            content["active"] += int(active or conversation_active)
            content["shared"] += int(shared)
            content["within_ttl"] += int(within_ttl)
            content["unknown"] += int(unknown)
            timeline_protected = (
                active
                or finished is None
                or bool(timeline_policy.get("legal_hold", False))
                or now < finished + timedelta(days=timeline_days)
            )
            timeline["protected" if timeline_protected else "eligible"] += 1
            receipt_expired = (
                row["receipt_expires_at"] is not None
                and now >= row["receipt_expires_at"]
                and not receipt_policy.get("legal_hold", False)
            )
            receipt["expired" if receipt_expired else "protected"] += 1
        return {
            "scanned": len(rows),
            "eligible": content["eligible"],
            "protected": content["protected"],
            "content": {**content, "retention_days": content_days},
            "timeline": {**timeline, "retention_days": timeline_days},
            "receipts": receipt,
            "bounded_batch": True,
        }

    async def object_is_referenced(
        self, session: AsyncSession, *, tenant_id: str, workspace_id: str, ref: str
    ) -> bool:
        return bool(
            (
                await session.execute(
                    text("""
            SELECT EXISTS(SELECT 1 FROM runtime.runs WHERE tenant_id=:tenant AND workspace_id=:workspace AND input_ref=:ref)
                OR EXISTS(SELECT 1 FROM runtime.messages WHERE tenant_id=:tenant AND workspace_id=:workspace AND body_ref=:ref)
                OR EXISTS(SELECT 1 FROM runtime.jobs WHERE tenant_id=:tenant AND workspace_id=:workspace AND payload_ref=:ref)
        """),
                    {"tenant": UUID(tenant_id), "workspace": UUID(workspace_id), "ref": ref},
                )
            ).scalar_one()
        )
