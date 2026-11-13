"""PostgreSQL-backed canonical Tool Gateway.

The gateway has no registration cache and no success fallback. Catalogs,
approval, idempotency and receipts are read or written only through DB-56.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from jsonschema import Draft202012Validator, ValidationError
from platform_common.principal import AuthenticatedPrincipal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.mcp.client import McpClientAdapter


class ToolExecutionError(RuntimeError):
    pass


class ToolNotFoundError(ToolExecutionError):
    pass


class ToolSchemaValidationError(ToolExecutionError):
    pass


class ToolPolicyDeniedError(ToolExecutionError):
    pass


class ToolApprovalRequiredError(ToolExecutionError):
    pass


class ToolIdempotencyConflictError(ToolExecutionError):
    pass


class ToolAmbiguousOutcomeError(ToolExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    principal: AuthenticatedPrincipal
    run_id: str
    agent_version_id: str
    tool_version_id: str
    operation_key: str
    arguments: dict[str, Any]
    step_key: str
    approval_request_id: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: str
    operation_key: str
    replayed: bool
    result: dict[str, Any] | None
    external_reference: str | None = None


class ToolAdapter(Protocol):
    async def invoke(
        self, execution_config: dict[str, Any], arguments: dict[str, Any], operation_key: str
    ) -> tuple[dict[str, Any], str | None]:
        """Perform one reviewed external call or raise a typed error."""


class McpToolAdapter:
    def __init__(self, client: McpClientAdapter | None = None) -> None:
        self._client = client or McpClientAdapter()

    async def invoke(
        self, execution_config: dict[str, Any], arguments: dict[str, Any], operation_key: str
    ) -> tuple[dict[str, Any], str | None]:
        endpoint = execution_config.get("endpoint")
        remote_tool_name = execution_config.get("remote_tool_name")
        mapping = execution_config.get("approved_mapping")
        if (
            not isinstance(endpoint, str)
            or not isinstance(remote_tool_name, str)
            or not isinstance(mapping, dict)
        ):
            raise ToolPolicyDeniedError("Pinned MCP execution config is incomplete")
        mapping = {**mapping, "request_id": operation_key}
        result = await self._client.call_tool(endpoint, remote_tool_name, arguments, mapping)
        external_reference = (
            result.get("_meta", {}).get("operation_id")
            if isinstance(result.get("_meta"), dict)
            else None
        )
        return result, external_reference if isinstance(external_reference, str) else None


class ToolDispatcher:
    """A fail-closed, async dispatcher backed by PostgreSQL transactions."""

    def __init__(
        self, session_factory: Any, adapters: dict[str, ToolAdapter] | None = None
    ) -> None:
        if session_factory is None:
            raise ValueError("ToolDispatcher requires an async PostgreSQL session factory")
        self._session_factory = session_factory
        self._adapters = adapters or {"mcp": McpToolAdapter()}

    @staticmethod
    def _canonical(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def compute_action_hash(cls, invocation: ToolInvocation) -> str:
        material = {
            "tenant_id": invocation.principal.tenant_id,
            "workspace_id": invocation.principal.workspace_id,
            "run_id": invocation.run_id,
            "step_key": invocation.step_key,
            "agent_version_id": invocation.agent_version_id,
            "tool_version_id": invocation.tool_version_id,
            "arguments": invocation.arguments,
        }
        return hashlib.sha256(cls._canonical(material).encode("utf-8")).hexdigest()

    @staticmethod
    async def _scope(session: AsyncSession, principal: AuthenticatedPrincipal) -> None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :value, true)"), {"value": principal.tenant_id}
        )
        await session.execute(
            text("SELECT set_config('app.workspace_id', :value, true)"),
            {"value": principal.workspace_id},
        )

    @staticmethod
    def _uuid(value: str, name: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ToolPolicyDeniedError(f"Invalid trusted {name}") from exc

    async def _resolve(self, session: AsyncSession, call: ToolInvocation) -> dict[str, Any]:
        # Re-check live tenant/workspace/membership state inside this DB transaction before tool grants.
        live_membership = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM iam.resolve_user_workspace_membership("
                        "CAST(:user_id AS uuid), CAST(:workspace_id AS uuid))"
                    ),
                    {
                        "user_id": self._uuid(call.principal.user_id, "user_id"),
                        "workspace_id": self._uuid(call.principal.workspace_id, "workspace_id"),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            live_membership is None
            or live_membership.get("tenant_status", "active") != "active"
            or live_membership.get("workspace_status", "active") != "active"
            or str(live_membership["tenant_id"]) != call.principal.tenant_id
        ):
            raise ToolPolicyDeniedError("Active workspace membership required for tool execution")

        live_role = str(live_membership["role"])
        effective_roles = (
            [r for r in call.principal.roles if r == live_role]
            if call.principal.roles
            else [live_role]
        )
        if not effective_roles:
            raise ToolPolicyDeniedError("Caller role no longer matches live workspace membership")

        result = await session.execute(
            text("""
                SELECT t.id AS tool_id, t.kind, tv.id AS tool_version_id, tv.input_schema,
                       tv.output_schema, tv.execution_config, tv.risk, tv.sha256, b.usage_mode
                FROM catalog.agent_tool_bindings AS b
                JOIN catalog.tool_versions AS tv
                  ON tv.id = b.tool_version_id AND tv.tenant_id = b.tenant_id AND tv.workspace_id = b.workspace_id
                JOIN catalog.tools AS t
                  ON t.id = tv.tool_id AND t.tenant_id = tv.tenant_id AND t.workspace_id = tv.workspace_id
                WHERE b.tenant_id = CAST(:tenant_id AS uuid)
                  AND b.workspace_id = CAST(:workspace_id AS uuid)
                  AND b.agent_version_id = CAST(:agent_version_id AS uuid)
                  AND b.tool_version_id = CAST(:tool_version_id AS uuid)
            """),
            {
                "tenant_id": call.principal.tenant_id,
                "workspace_id": call.principal.workspace_id,
                "agent_version_id": self._uuid(call.agent_version_id, "agent_version_id"),
                "tool_version_id": self._uuid(call.tool_version_id, "tool_version_id"),
            },
        )
        tool = result.mappings().one_or_none()
        if tool is None:
            raise ToolNotFoundError("Pinned tool version is not bound to this agent version")
        grant = await session.execute(
            text("""
                SELECT 1 FROM catalog.tool_grants
                WHERE tenant_id = CAST(:tenant_id AS uuid) AND workspace_id = CAST(:workspace_id AS uuid)
                  AND tool_id = CAST(:tool_id AS uuid) AND allowed_operation = 'invoke'
                  AND principal_kind = 'role' AND principal_key = ANY(CAST(:roles AS text[]))
                LIMIT 1
            """),
            {
                "tenant_id": call.principal.tenant_id,
                "workspace_id": call.principal.workspace_id,
                "tool_id": str(tool["tool_id"]),
                "roles": effective_roles,
            },
        )
        if grant.scalar_one_or_none() is None:
            raise ToolPolicyDeniedError("No active server-side tool grant for this principal")
        resolved = dict(tool)
        if resolved["kind"] != "mcp":
            return resolved
        mapping_result = await session.execute(
            text("""
                SELECT m.status AS mapping_status, m.remote_tool_name, m.approved_schema_sha256,
                       item.schema_sha256 AS discovered_schema_sha256, c.status AS connection_status,
                       c.config, c.secret_ref
                FROM integration.mcp_tool_mappings AS m
                JOIN integration.mcp_discovered_items AS item
                  ON item.id = m.discovery_item_id AND item.tenant_id = m.tenant_id AND item.workspace_id = m.workspace_id
                JOIN integration.connections AS c
                  ON c.id = m.connection_id AND c.tenant_id = m.tenant_id AND c.workspace_id = m.workspace_id
                WHERE m.tenant_id = CAST(:tenant_id AS uuid) AND m.workspace_id = CAST(:workspace_id AS uuid)
                  AND m.tool_version_id = CAST(:tool_version_id AS uuid)
            """),
            {
                "tenant_id": call.principal.tenant_id,
                "workspace_id": call.principal.workspace_id,
                "tool_version_id": str(resolved["tool_version_id"]),
            },
        )
        mapping = mapping_result.mappings().one_or_none()
        if (
            mapping is None
            or mapping["mapping_status"] != "approved"
            or mapping["connection_status"] != "active"
        ):
            raise ToolPolicyDeniedError("MCP mapping or connection is not active and approved")
        if not isinstance(mapping["discovered_schema_sha256"], str) or (
            mapping["approved_schema_sha256"].strip() != mapping["discovered_schema_sha256"].strip()
        ):
            raise ToolPolicyDeniedError("MCP mapping schema has drifted since review")
        if mapping["secret_ref"] is not None:
            raise ToolPolicyDeniedError(
                "MCP connection requires an approved server-side secret resolver"
            )
        connection_config = dict(mapping["config"])
        endpoint = connection_config.get("endpoint")
        if not isinstance(endpoint, str):
            raise ToolPolicyDeniedError("MCP connection config is missing endpoint")
        resolved["execution_config"] = {
            "endpoint": endpoint,
            "remote_tool_name": mapping["remote_tool_name"],
            "approved_mapping": {
                "status": mapping["mapping_status"],
                "connection_status": mapping["connection_status"],
                "approved_schema_sha256": mapping["approved_schema_sha256"].strip(),
                "discovered_schema_sha256": mapping["discovered_schema_sha256"].strip(),
            },
        }
        return resolved

    async def _verify_approval(
        self, session: AsyncSession, call: ToolInvocation, tool: dict[str, Any]
    ) -> None:
        needs_approval = (
            tool["risk"] in ("write", "high_impact") or tool["usage_mode"] == "requires_approval"
        )
        if not needs_approval:
            return
        if call.approval_request_id is None:
            raise ToolApprovalRequiredError("This tool requires an approved action-bound request")
        action_hash = self.compute_action_hash(call)
        result = await session.execute(
            text("""
                SELECT status, action_hash, expires_at
                FROM runtime.approval_requests
                WHERE id = CAST(:approval_id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
                  AND workspace_id = CAST(:workspace_id AS uuid) AND run_id = CAST(:run_id AS uuid)
                  AND step_key = :step_key AND expires_at > now()
            """),
            {
                "approval_id": self._uuid(call.approval_request_id, "approval_request_id"),
                "tenant_id": call.principal.tenant_id,
                "workspace_id": call.principal.workspace_id,
                "run_id": self._uuid(call.run_id, "run_id"),
                "step_key": call.step_key,
            },
        )
        approval = result.mappings().one_or_none()
        if approval is None or approval["status"] != "approved":
            raise ToolApprovalRequiredError("No current approved action exists")
        if approval["action_hash"].strip() != action_hash:
            raise ToolApprovalRequiredError(
                "Approved action does not match the canonical invocation"
            )

    async def _reauthorize_before_execution(
        self,
        call: ToolInvocation,
    ) -> dict[str, Any]:
        """Recheck live membership, grants, mapping, and approval immediately before dispatch."""
        async with self._session_factory() as session, session.begin():
            await self._scope(session, call.principal)
            tool = await self._resolve(session, call)
            try:
                Draft202012Validator(tool["input_schema"]).validate(call.arguments)
            except ValidationError as exc:
                raise ToolSchemaValidationError(
                    "Tool arguments do not match the currently authorized pinned schema"
                ) from exc
            await self._verify_approval(session, call, tool)
            return tool

    async def _reserve(
        self, call: ToolInvocation, tool: dict[str, Any], request_hash: str
    ) -> ToolResult | None:
        async with self._session_factory() as session, session.begin():
            await self._scope(session, call.principal)
            inserted = await session.execute(
                text("""
                        INSERT INTO runtime.tool_operations
                            (tenant_id, workspace_id, run_id, tool_version_id, operation_key, request_hash, status)
                        VALUES (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), CAST(:run_id AS uuid),
                                CAST(:tool_version_id AS uuid), :operation_key, :request_hash, 'reserved')
                        ON CONFLICT (tenant_id, workspace_id, operation_key) DO NOTHING
                        RETURNING id
                    """),
                {
                    "tenant_id": call.principal.tenant_id,
                    "workspace_id": call.principal.workspace_id,
                    "run_id": self._uuid(call.run_id, "run_id"),
                    "tool_version_id": str(tool["tool_version_id"]),
                    "operation_key": call.operation_key,
                    "request_hash": request_hash,
                },
            )
            if inserted.scalar_one_or_none() is not None:
                return None
            existing = (
                (
                    await session.execute(
                        text("""
                        SELECT request_hash, status, external_reference FROM runtime.tool_operations
                        WHERE tenant_id = CAST(:tenant_id AS uuid) AND workspace_id = CAST(:workspace_id AS uuid)
                          AND operation_key = :operation_key FOR UPDATE
                    """),
                        {
                            "tenant_id": call.principal.tenant_id,
                            "workspace_id": call.principal.workspace_id,
                            "operation_key": call.operation_key,
                        },
                    )
                )
                .mappings()
                .one()
            )
            if existing["request_hash"].strip() != request_hash:
                raise ToolIdempotencyConflictError(
                    "operation_key was previously used for different arguments"
                )
            if existing["status"] == "succeeded":
                return ToolResult(
                    "succeeded", call.operation_key, True, None, existing["external_reference"]
                )
            if existing["status"] == "unknown":
                raise ToolAmbiguousOutcomeError(
                    "Operation outcome is unknown and requires reconciliation"
                )
            raise ToolIdempotencyConflictError("Operation is already reserved or executing")

    async def _transition(
        self, call: ToolInvocation, status: str, *, external_reference: str | None = None
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await self._scope(session, call.principal)
            result = await session.execute(
                text("""
                        UPDATE runtime.tool_operations
                        SET status = :status, last_attempt_at = now(), external_reference = :external_reference
                        WHERE tenant_id = CAST(:tenant_id AS uuid) AND workspace_id = CAST(:workspace_id AS uuid)
                          AND operation_key = :operation_key
                          AND status = CASE WHEN :status = 'executing' THEN 'reserved' ELSE 'executing' END
                    """),
                {
                    "status": status,
                    "external_reference": external_reference,
                    "tenant_id": call.principal.tenant_id,
                    "workspace_id": call.principal.workspace_id,
                    "operation_key": call.operation_key,
                },
            )
            if result.rowcount != 1:
                raise ToolExecutionError("Tool operation state transition was lost")
            if status in {"succeeded", "failed", "unknown"}:
                sequence_result = await session.execute(
                    text("""
                            UPDATE runtime.runs
                            SET next_event_seq = next_event_seq + 1
                            WHERE id = CAST(:run_id AS uuid)
                              AND tenant_id = CAST(:tenant_id AS uuid)
                              AND workspace_id = CAST(:workspace_id AS uuid)
                            RETURNING next_event_seq - 1 AS sequence
                        """),
                    {
                        "run_id": self._uuid(call.run_id, "run_id"),
                        "tenant_id": call.principal.tenant_id,
                        "workspace_id": call.principal.workspace_id,
                    },
                )
                sequence = sequence_result.scalar_one_or_none()
                if sequence is None:
                    raise ToolExecutionError("Run disappeared before terminal tool receipt")
                receipt = {"operation_key": call.operation_key, "status": status}
                await session.execute(
                    text("""
                            UPDATE runtime.tool_operations
                            SET result_ref = :result_ref
                            WHERE tenant_id = CAST(:tenant_id AS uuid)
                              AND workspace_id = CAST(:workspace_id AS uuid)
                              AND operation_key = :operation_key
                        """),
                    {
                        "result_ref": f"run_event:{sequence}",
                        "tenant_id": call.principal.tenant_id,
                        "workspace_id": call.principal.workspace_id,
                        "operation_key": call.operation_key,
                    },
                )
                await session.execute(
                    text("""
                            INSERT INTO runtime.run_events
                                (tenant_id, workspace_id, run_id, sequence, event_type, redacted_payload)
                            VALUES (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), CAST(:run_id AS uuid),
                                    :sequence, 'tool.operation.receipt', CAST(:payload AS jsonb))
                        """),
                    {
                        "tenant_id": call.principal.tenant_id,
                        "workspace_id": call.principal.workspace_id,
                        "run_id": self._uuid(call.run_id, "run_id"),
                        "sequence": sequence,
                        "payload": self._canonical(receipt),
                    },
                )
                await session.execute(
                    text("""
                            INSERT INTO governance.audit_events
                                (tenant_id, workspace_id, actor_subject, action, resource_type, resource_id,
                                 outcome, trace_id, details_redacted)
                            VALUES (CAST(:tenant_id AS uuid), CAST(:workspace_id AS uuid), :actor_subject,
                                    'tool.invoke', 'runtime.tool_operations', :operation_key, :outcome,
                                    :trace_id, CAST(:details AS jsonb))
                        """),
                    {
                        "tenant_id": call.principal.tenant_id,
                        "workspace_id": call.principal.workspace_id,
                        "actor_subject": call.principal.user_id,
                        "operation_key": call.operation_key,
                        "outcome": "succeeded" if status == "succeeded" else "failed",
                        "trace_id": call.trace_id,
                        "details": self._canonical(receipt),
                    },
                )

    async def execute(self, call: ToolInvocation) -> ToolResult:
        self._uuid(call.run_id, "run_id")
        if not call.operation_key or len(call.operation_key) > 200:
            raise ToolSchemaValidationError("operation_key is required and must be bounded")
        request_hash = hashlib.sha256(self._canonical(call.arguments).encode("utf-8")).hexdigest()
        async with self._session_factory() as session, session.begin():
            await self._scope(session, call.principal)
            tool = await self._resolve(session, call)
            try:
                Draft202012Validator(tool["input_schema"]).validate(call.arguments)
            except ValidationError as exc:
                raise ToolSchemaValidationError(
                    "Tool arguments do not match the pinned schema"
                ) from exc
            await self._verify_approval(session, call, tool)
        prior = await self._reserve(call, tool, request_hash)
        if prior is not None:
            return prior
        await self._transition(call, "executing")
        try:
            tool = await self._reauthorize_before_execution(call)
        except Exception:
            await self._transition(call, "failed")
            raise
        adapter = self._adapters.get(tool["kind"])
        if adapter is None:
            await self._transition(call, "failed")
            raise ToolPolicyDeniedError("No reviewed adapter is registered for this tool kind")
        config = dict(tool["execution_config"])
        try:
            result, external_reference = await adapter.invoke(
                config, call.arguments, call.operation_key
            )
            Draft202012Validator(tool["output_schema"]).validate(result)
        except Exception:
            await self._transition(
                call, "unknown" if tool["risk"] in ("write", "high_impact") else "failed"
            )
            raise
        await self._transition(call, "succeeded", external_reference=external_reference)
        return ToolResult("succeeded", call.operation_key, False, result, external_reference)
