# SPEC-TOOL-001 — Shared Tool Registry, Gateway, Permission & Idempotency

**Status:** DESIGN FOR IMPLEMENTATION · **DB:** `DB-BASELINE-56` · **Priority:** P0 · **Owners:** Control API (registry); Agent Runtime (gateway)

## 1. Product goal and non-goals

Một công cụ có thể là `http`, `mcp` hoặc `builtin`; **tất cả đi qua một Tool Gateway** với schema validation, authorization, risk classification, HITL, idempotency, timeout, output filtering, audit và tracing. Không tự xây MCP Tool Executor hoặc Skill Tool Executor riêng.

Không cho phép: LLM tự tạo tool, gán quyền tool, sửa mapping đã duyệt, chạy shell bất kỳ, ghi hệ thống bên ngoài mà không cần policy, hoặc coi Skill `allowed-tools` là authorization.

## 2. DB 56 mapping (KHÔNG thêm bảng)

| Table | Use |
|---|---|
| `catalog.tools` | Canonical tool identity, `kind` in `http`,`mcp`,`builtin` |
| `catalog.tool_versions` | Immutable `input_schema`, `output_schema`, `execution_config`, `risk`, `sha256` |
| `catalog.tool_grants` | Principal grants `invoke/manage` |
| `catalog.agent_tool_bindings` | AgentVersion → ToolVersion, `usage_mode` |
| `runtime.tool_operations` | Operation reservation, request hash, unknown outcome, idempotency |
| `runtime.approval_requests`, `approval_decisions` | Approval for write/high-impact |
| `runtime.run_events`, `governance.audit_events` | Evidence and redacted timeline |
| `integration.connections` | Existing MCP Server / HTTP credential endpoint reference |
| `integration.mcp_discovery_snapshots`, `integration.mcp_discovered_items`, `integration.mcp_tool_mappings` | **Three MCP tables in 56**: snapshot→reviewed tool mapping |
| `catalog.skills`,`catalog.skill_versions`,`catalog.agent_skill_bindings` | **Three Skill tables in 56**: guidance, never direct grant |

`tool_versions` is source of truth for validated schemas and risk. Network credentials must not be serialized in `execution_config`; store `secret_ref` with access restricted to server-side secret resolver.

## 3. ToolSpec & runtime input

```json
{
  "schemaVersion": "1.0",
  "toolKey": "quotation-draft",
  "kind": "http",
  "version": 1,
  "risk": "write",
  "inputSchema": {"type":"object","additionalProperties":false,"properties":{"requestId":{"type":"string"}},"required":["requestId"]},
  "outputSchema": {"type":"object","properties":{"draftId":{"type":"string"}},"required":["draftId"]},
  "executionConfig": {"connectionRef":"connection-id","method":"POST","path":"/drafts","timeoutMs":10000}
}
```

This is an illustrative JSON contract, not an already shipped schema. `contracts/jsonschema/tool-spec.v1.schema.json` is authoritative. Canonical schema hash excludes mutable labels, is recomputed at approval and before invocation. Reject unknown fields, uncontrolled URL targets, arbitrary headers, unexpected methods, oversized requests, and unsupported JSON Schema constructs if validator cannot enforce them.

## 4. Gateway pipeline — fail closed

```text
Agent Runtime emits ToolIntent (not permission)
 -> trusted tenant/workspace/run/agent_version resolved
 -> find pinned tool_version + tool grant + active binding
 -> if MCP: connection enabled + mapping status approved + schema SHA still matching
 -> re-evaluate policy/role and risk classification
 -> validate arguments against pinned input_schema
 -> for write/high_impact: exact approval for canonical action_hash if required
 -> reserve operation_key + request_hash in runtime.tool_operations transaction
 -> acquire credential by secret_ref, apply outbound egress/SSRF protection
 -> execute via adapter, bounded timeout & response bytes
 -> validate output_schema, strip secrets/untrusted markup, store redacted result
 -> mark operation succeeded/failed/unknown + emit auditable event
 -> return structured ToolResult to graph
```

No direct call from browser to remote MCP server. No bypass from Skill scripts. No reuse of credential across tenants. No full secret values in debug logs or exception text.

## 5. Tool execution semantics

`runtime.tool_operations.status`: `reserved`, `executing`, `succeeded`, `failed`, `unknown` (exact existing DB `CHECK`). One unique `(tenant_id,workspace_id,operation_key)`.

- `read`: safe to retry according to provider behavior; caching only with tenant and authorization context, cache key also includes tool version and schema hash.
- `write`: must use operation idempotency key accepted by upstream where possible. If request times out after sending, store `unknown` and reconcile before any repeat.
- `high_impact`: requires human approval policy plus strongly controlled permission; approval references **exact action hash**, not a vague tool name.
- Retry same operation key, same hash: return stored terminal outcome or reconciliation state; different hash: reject 409 and audit. **No exactly-once claim** without downstream idempotency contract.
- Tool registry changes do not retroactively rewrite a run's pinned version, but connection/permission revocation blocks new invocations immediately.

## 6. MCP-specific boundary

MCP Client Management remains per `CR-MCP-001` and MCP spec `2026-07-28`. Gateway should consume approved mappings, not perform implicit discovery on `tools/call`. Current MCP is stateless protocol core; backward compatibility to older handshake eras is the **MCP SDK adapter's** responsibility. Mapped remote tool still uses `catalog.tool_versions` and normal `ToolResult` type.

For `integration.mcp_tool_mappings` approved rows, check: `(tenant,workspace,connection,discovery_item_id)` points to `item_kind='tool'`; `remote_key=remote_tool_name`; `approved_schema_sha256` matches discovered tool and `tool_versions` schema; `catalog.tools.kind='mcp'`; connection active; mapping not stale/revoked. Composite FK alone does not validate these semantic conditions.

## 7. Required source delta

```text
contracts/jsonschema/tool-spec.v1.schema.json
contracts/jsonschema/tool-call.v1.schema.json
contracts/openapi/control-api.openapi.yaml
apps/control-api/src/control_api/modules/tools/
  api/{router.py,requests.py,responses.py}
  application/{register_tool.py,publish_tool_version.py,grant_tool.py,bind_agent_tool.py}
  domain/{rules.py,models.py}
  infrastructure/{repository.py,orm_models.py}
apps/agent-runtime/src/agent_runtime/tools/
  {gateway.py,dispatcher.py,validation.py,idempotency.py,output_sanitizer.py}
  adapters/{http.py,builtin.py}
apps/agent-runtime/src/agent_runtime/mcp/    # CR-MCP-001 owns adapter
```

No second `mcp_tools` business table or extra privileged service. `gateway.py` orchestrates adapters using a common interface `invoke(ToolCallContext, args) -> ToolResult`.

## 8. Control API endpoints and permissions

| Method + route | Permission | Contract |
|---|---|---|
| `POST /api/v1/workspaces/{id}/tools` | `tool:create` | Register tool draft with risk assessment |
| `GET /api/v1/workspaces/{id}/tools` | `tool:read` | List metadata (no secrets) |
| `POST /api/v1/workspaces/{id}/tools/{tool_id}/versions` | `tool:publish` | Freeze validated version |
| `POST /api/v1/workspaces/{id}/tools/{tool_id}/grants` | `tool:grant` | Explicit role or service account grant |
| `POST /api/v1/workspaces/{id}/agents/{agent_id}/tool-bindings` | `agent:edit` + `tool:invoke` | Bind allowed ToolVersion in draft/publish flow |

Actual endpoint naming must match OpenAPI decided in API-01. These are target contracts, not implemented routes. Changes to live policy must be audited; unauthorized users get no capability discovery details.

## 9. Security and reliability test matrix

| ID | Input | Expected |
|---|---|---|
| TOOL-001 | Tool name not in published binding | Denied, no network call |
| TOOL-002 | Skill text requests hidden extra Tool | Denied, Skill is not a grant |
| TOOL-003 | Invalid JSON arguments | 422/ToolError, no call |
| TOOL-004 | Unauthorized tenant/workspace | Denied, no resource leak |
| TOOL-005 | MCP mapping `stale` or changed schema | Denied and audit |
| TOOL-006 | OAuth token missing/expired | Fail closed, no retry to another tenant |
| TOOL-007 | Write timeout after request sent | `unknown`, reconciliation required |
| TOOL-008 | Retry same operation_key with changed hash | 409, no second write |
| TOOL-009 | High-impact tool without approved action hash | waiting approval, no side effect |
| TOOL-010 | Private IP redirect/DNS rebinding | SSRF guard blocks egress |
| TOOL-011 | Prompt injection in tool response | Treated as untrusted data, sanitized |
| TOOL-012 | Retry after worker crash | No double side effect in controlled test |

## 10. PRs and Definition of Done

`TL-PR01`: canonical ToolSpec, validation + contracts; `TL-PR02`: Registry & ToolVersion + grants API; `TL-PR03`: Gateway authz + risk + idempotency reservation; `TL-PR04`: HTTP/Builtin adapters, sanitizer, receipt persistence; `TL-PR05`: wire MCP approved mapping and Skills; `TL-PR06`: failure injection, secrets/SSRF, concurrency tests.

**Exit:** all tool calls pass shared Gateway; unknown side effects never blind-retry; authority separation preserved; no additional domain table outside 56.

## References
- MCP 2026-07-28: https://blog.modelcontextprotocol.io/posts/2026-07-28/
- Agno component registry: https://docs.agno.com/agent-os/studio/registry
- PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
