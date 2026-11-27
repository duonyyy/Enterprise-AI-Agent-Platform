"""PostgreSQL repository for durable run data, conversations, messages, and retention (06-A-T03).

Operates strictly on the verified baseline schema (0001..0008) using caller-owned
AsyncSession transactions with PostgreSQL RLS context already bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.application.run_data import RunDataValidationError

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
ACTIVE_RUN_STATUSES: frozenset[str] = frozenset({"queued", "running", "waiting_approval"})
RUN_CONTENT_DATA_CLASS = "run_content"


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: str
    tenant_id: str
    workspace_id: str
    external_subject: str | None
    status: str
    created_at: datetime
    last_activity_at: datetime


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: str
    tenant_id: str
    workspace_id: str
    conversation_id: str
    role: str
    body_ref: str | None
    body_ciphertext: bytes | None
    token_count: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunDataRecord:
    id: str
    tenant_id: str
    workspace_id: str
    agent_version_id: str
    conversation_id: str | None
    initiated_by: str | None
    external_request_key: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RetentionPolicyRecord:
    data_class: str
    retention_days: int
    legal_hold: bool


class RunDataRepository:
    """Caller-session-scoped repository for runtime conversations, messages, runs, and retention."""

    async def create_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        creator_user_id: str,
        conversation_id: str | None = None,
    ) -> str:
        conv_id = str(UUID(conversation_id)) if conversation_id else str(uuid4())
        owner_sub = str(UUID(creator_user_id))
        await session.execute(
            text("""
                INSERT INTO runtime.conversations (
                    tenant_id, workspace_id, id, external_subject, status, created_at, last_activity_at
                ) VALUES (
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:id AS uuid),
                    :external_subject,
                    'active',
                    now(),
                    now()
                )
            """),
            {
                "tenant_id": str(UUID(tenant_id)),
                "workspace_id": str(UUID(workspace_id)),
                "id": conv_id,
                "external_subject": owner_sub,
            },
        )
        return conv_id

    async def get_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        row = (
            (
                await session.execute(
                    text("""
                        SELECT id, tenant_id, workspace_id, external_subject, status,
                               created_at, last_activity_at
                        FROM runtime.conversations
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND id = CAST(:conversation_id AS uuid)
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "conversation_id": str(UUID(conversation_id)),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return ConversationRecord(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            workspace_id=str(row["workspace_id"]),
            external_subject=row["external_subject"],
            status=str(row["status"]),
            created_at=row["created_at"],
            last_activity_at=row["last_activity_at"],
        )

    async def append_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        role: str,
        body_ref: str | None = None,
        body_ciphertext: bytes | None = None,
        token_count: int | None = None,
        message_id: str | None = None,
    ) -> str:
        if role not in ("user", "assistant", "tool", "system"):
            raise RunDataValidationError(f"Invalid message role: {role}")
        if (body_ref is not None) == (body_ciphertext is not None):
            raise RunDataValidationError(
                "Message must provide exactly one of body_ref or body_ciphertext"
            )
        msg_id = str(UUID(message_id)) if message_id else str(uuid4())
        await session.execute(
            text("""
                INSERT INTO runtime.messages (
                    tenant_id, workspace_id, id, conversation_id, role,
                    body_ref, body_ciphertext, token_count, created_at
                ) VALUES (
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:id AS uuid),
                    CAST(:conversation_id AS uuid),
                    :role,
                    :body_ref,
                    :body_ciphertext,
                    :token_count,
                    now()
                )
            """),
            {
                "tenant_id": str(UUID(tenant_id)),
                "workspace_id": str(UUID(workspace_id)),
                "id": msg_id,
                "conversation_id": str(UUID(conversation_id)),
                "role": role,
                "body_ref": body_ref,
                "body_ciphertext": body_ciphertext,
                "token_count": token_count,
            },
        )
        await session.execute(
            text("""
                UPDATE runtime.conversations
                SET last_activity_at = now()
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid)
                  AND id = CAST(:conversation_id AS uuid)
            """),
            {
                "tenant_id": str(UUID(tenant_id)),
                "workspace_id": str(UUID(workspace_id)),
                "conversation_id": str(UUID(conversation_id)),
            },
        )
        return msg_id

    async def list_conversation_messages(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        limit: int = 100,
    ) -> list[MessageRecord]:
        bounded_limit = max(1, min(limit, 500))
        rows = (
            (
                await session.execute(
                    text("""
                        SELECT id, tenant_id, workspace_id, conversation_id, role,
                               body_ref, body_ciphertext, token_count, created_at
                        FROM runtime.messages
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND conversation_id = CAST(:conversation_id AS uuid)
                        ORDER BY created_at ASC, id ASC
                        LIMIT :limit
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "conversation_id": str(UUID(conversation_id)),
                        "limit": bounded_limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            MessageRecord(
                id=str(r["id"]),
                tenant_id=str(r["tenant_id"]),
                workspace_id=str(r["workspace_id"]),
                conversation_id=str(r["conversation_id"]),
                role=str(r["role"]),
                body_ref=r["body_ref"],
                body_ciphertext=bytes(r["body_ciphertext"])
                if r["body_ciphertext"] is not None
                else None,
                token_count=r["token_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def activate_run_input(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
        agent_version_id: str,
        initiated_by: str,
        external_request_key: str,
        input_ref: str,
        conversation_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        """Activate a pre-verified run input object into runtime.runs, runtime.jobs, and optional message."""
        t_id = str(UUID(tenant_id))
        w_id = str(UUID(workspace_id))
        r_id = str(UUID(run_id))
        av_id = str(UUID(agent_version_id))
        u_id = str(UUID(initiated_by))
        c_id = str(UUID(conversation_id)) if conversation_id else None
        j_id = str(UUID(job_id)) if job_id else str(uuid4())

        await session.execute(
            text("""
                INSERT INTO runtime.runs (
                    id, tenant_id, workspace_id, agent_version_id, conversation_id,
                    initiated_by, external_request_key, status, next_event_seq, created_at
                ) VALUES (
                    CAST(:run_id AS uuid),
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:agent_version_id AS uuid),
                    CAST(:conversation_id AS uuid),
                    CAST(:initiated_by AS uuid),
                    :external_request_key,
                    'queued',
                    2,
                    now()
                )
            """),
            {
                "run_id": r_id,
                "tenant_id": t_id,
                "workspace_id": w_id,
                "agent_version_id": av_id,
                "conversation_id": c_id,
                "initiated_by": u_id,
                "external_request_key": external_request_key,
            },
        )

        await session.execute(
            text("""
                INSERT INTO runtime.jobs (
                    id, tenant_id, workspace_id, run_id, job_type, payload_ref, status, created_at
                ) VALUES (
                    CAST(:job_id AS uuid),
                    CAST(:tenant_id AS uuid),
                    CAST(:workspace_id AS uuid),
                    CAST(:run_id AS uuid),
                    'run',
                    :payload_ref,
                    'queued',
                    now()
                )
            """),
            {
                "job_id": j_id,
                "tenant_id": t_id,
                "workspace_id": w_id,
                "run_id": r_id,
                "payload_ref": input_ref,
            },
        )

        if c_id is not None:
            await self.append_message(
                session,
                tenant_id=t_id,
                workspace_id=w_id,
                conversation_id=c_id,
                role="user",
                body_ref=input_ref,
            )

        return j_id

    async def get_run_record(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> RunDataRecord | None:
        row = (
            (
                await session.execute(
                    text("""
                        SELECT id, tenant_id, workspace_id, agent_version_id, conversation_id,
                               initiated_by, external_request_key, status,
                               started_at, finished_at, created_at
                        FROM runtime.runs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND id = CAST(:run_id AS uuid)
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "run_id": str(UUID(run_id)),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return RunDataRecord(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            workspace_id=str(row["workspace_id"]),
            agent_version_id=str(row["agent_version_id"]),
            conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
            initiated_by=str(row["initiated_by"]) if row["initiated_by"] else None,
            external_request_key=row["external_request_key"],
            status=str(row["status"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            created_at=row["created_at"],
        )

    async def get_run_input_ref(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str,
    ) -> str | None:
        row = (
            (
                await session.execute(
                    text("""
                        SELECT payload_ref
                        FROM runtime.jobs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND run_id = CAST(:run_id AS uuid)
                          AND job_type = 'run'
                          AND payload_ref IS NOT NULL
                        ORDER BY created_at ASC, id ASC
                        LIMIT 1
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "run_id": str(UUID(run_id)),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return str(row["payload_ref"]) if row is not None and row["payload_ref"] else None

    async def get_retention_policy(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        data_class: str = RUN_CONTENT_DATA_CLASS,
        default_retention_days: int = 30,
    ) -> RetentionPolicyRecord:
        row = (
            (
                await session.execute(
                    text("""
                        SELECT data_class, retention_days, legal_hold
                        FROM governance.retention_policies
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND data_class = :data_class
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "data_class": data_class,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return RetentionPolicyRecord(
                data_class=data_class,
                retention_days=default_retention_days,
                legal_hold=False,
            )
        return RetentionPolicyRecord(
            data_class=str(row["data_class"]),
            retention_days=int(row["retention_days"]),
            legal_hold=bool(row["legal_hold"]),
        )

    async def has_active_runs_for_conversation(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> bool:
        row = (
            (
                await session.execute(
                    text("""
                        SELECT 1 AS active
                        FROM runtime.runs
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND workspace_id = CAST(:workspace_id AS uuid)
                          AND conversation_id = CAST(:conversation_id AS uuid)
                          AND status IN ('queued', 'running', 'waiting_approval')
                        LIMIT 1
                    """),
                    {
                        "tenant_id": str(UUID(tenant_id)),
                        "workspace_id": str(UUID(workspace_id)),
                        "conversation_id": str(UUID(conversation_id)),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return row is not None
