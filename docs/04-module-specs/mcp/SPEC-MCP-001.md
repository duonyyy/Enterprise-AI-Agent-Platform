# SPEC-MCP-001 — MCP Client Management, Tool Discovery & Controlled Invocation
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P1 · **Owner:** Control API MCP management + Runtime MCP adapter / central Tool Gateway

## 1. Mục tiêu, phụ thuộc và ranh giới

MCP Management là layer tích hợp, **không phải Tool Registry thứ hai**. Đăng ký remote MCP connection, khám phá tools/resources/prompts, review schema drift, tạo mapping đã phê duyệt tới existing ToolVersion và route actual tool calls duy nhất qua shared Tool Gateway. MVP triển khai **MCP Client** Streamable HTTP; MCP Server publishing là phase sau có ADR riêng. Chuẩn MCP target `2026-07-28` stateless; adapter kiểm chứng tương thích 2025-11-25 khi cần.

**Phụ thuộc:** `SPEC-TOOL-001`, `SPEC-SECURITY-001`, `SPEC-AGENT-001`, `SPEC-RUNTIME-001`; detailed contract `references/CR-MCP-001.md`

**Không nằm trong scope:** A second Tool Registry/Gateway, auto-import/execute all remote tools, remote-code execution via stdio, MCP Server publishing in MVP, granting Skill authority via MCP resources, assuming a remote server is trustworthy because it supports MCP.

## 2. Mapping sang CSDL 56 bảng

| Table | Use |
|---|---|
| `integration.connections` (existing) | canonical MCP server connection/credential ref/status |
| `integration.mcp_discovery_snapshots` (+1) | immutable discovery revision/protocol digest |
| `integration.mcp_discovered_items` (+1) | tools/resources/prompts metadata per snapshot |
| `integration.mcp_tool_mappings` (+1) | approved remote tool to `catalog.tool_versions`, schema hashes |
| `catalog.tools`, `tool_versions`, `tool_grants` | authoritative tool registry/grants (existing) |
| `catalog.agent_tool_bindings` | pinned ToolVersion; no `agent_mcp_bindings` |
| `runtime.tool_operations` | central idempotent execution receipts and `unknown` state |
| `governance.audit_events` | connection/grant/schema-approval trail |

SQL 3 new MCP tables authored in `references/CR-MCP-001.md` and should be implemented by additive migration with RLS; not proof of deployment.

## 3. Luồng hoạt động và domain invariants

1. Admin registers allowed remote URL and `secret_ref`, validates DNS/redirect/egress for SSRF; cannot type `file://` or arbitrary internal IP. Prefer OAuth policy through managed secret store.
2. Discovery client negotiates MCP `2026-07-28` per SDK, and version adapter for older protocols. For modern stateless core, do **not** depend on old global initialize/session handshake. Respect version/capability metadata and supported operations.
3. Snapshot tools/resources/prompts returned by server, preserve source/scheme/hash, with length/size limits. Tool schemas are **untrusted** until reviewed; descriptions cannot become higher-priority instructions.
4. Admin reviews tool mapping and schema digest; only approved remote tool is registered in `catalog.tool_versions`/`mcp_tool_mappings`. New remote tool or schema drift is pending/stale, not auto-enabled.
5. On Agent publish, pin ToolVersion through `agent_tool_bindings`; on runtime invocation: policy + grant + approval → gateway→MCP adapter→remote tool; verify remote tool name/schema approved for current connection.
6. Use scoped service credentials, outbound allowlist, request deadline, concurrency cap; normalise result to central `ToolResult`; never trust MCP response to alter grants or system policy.
7. For write operations, reuse tool_operations idempotency; if remote request outcome unknown, stop and reconcile; do not reissue just because HTTP connection lost.
8. Resources/prompts discovery can be shown read-only with policy; importing external Skills/prompts from MCP requires explicit separate security review and never auto-publish.

## 4. API contracts & authorization

Follow `references/CR-MCP-001.md` as detailed source for connection API, discovery, mapping, approval, refresh, health and tool schema diff. Workspace-scoped admin actions require `mcp:manage|discover|approve`; agent tool execution goes **only** through `SPEC-TOOL-001` gateway with `tool:invoke` authorization. No browser-to-MCP direct tool call. No general MCP Server Publishing endpoint in MVP.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

`McpConnectionRef {connectionId,protocolVersion,authMode,secretRef}`; `McpDiscoveryItem {snapshotId,itemKind,remoteKey,descriptorSha256,schemaSha256?}`; `ApprovedMcpToolMapping {toolVersionId,connectionId,remoteToolName,approvedSchemaSha256,status}`. For MCP spec 2026-07-28 message metadata/versioning, use pinned official SDK; compatibility adapter handles legacy variant rather than hardcode both in business code.

Reject schema drift, remote rename and malformed/oversized result. `tools/list` is discovery, not approval or permission; `tools/call` only when mapped and granted.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
apps/control-api/src/control_api/modules/mcp/{router.py,schemas.py,service.py,registry.py,discovery.py,repository.py}
apps/agent-runtime/src/agent_runtime/mcp/{client.py,connection_manager.py,tool_adapter.py,authorization.py}
apps/agent-runtime/src/agent_runtime/tools/gateway.py  # reuse; never make MCP executor here
apps/studio-web/src/features/mcp-management/{McpServerList.tsx,McpServerForm.tsx,McpToolExplorer.tsx,McpConnectionStatus.tsx}
contracts/jsonschema/{mcp-connection.v1.schema.json,mcp-discovery.v1.schema.json,mcp-tool-mapping.v1.schema.json}
tests/{contract/mcp,security/mcp,e2e/approved_mcp_tool.py}
```

## 7. Security, privacy và kiểm soát quyền

Out-of-band SSRF/egress controls at network layer, DNS revalidation on redirects, no local stdio subprocess by default. Credentials only in secret manager; OAuth audience/scope must match endpoint. Tool schemas are untrusted content and should be sanitized before model/tool context. Multi-tenant grant checks and mapping hashes every invocation. For high-impact tools require HITL exact action hash. Rate limit remote server and keep audit of mapping approval/revocation.

## 8. Lỗi, retry, idempotency và recovery

Remote MCP 401/403 terminal until credential refresh; network timeout with write -> `runtime.tool_operations.unknown`, not retry; safe reads may bounded retry. Discovery failure does not erase previous audited snapshot but can force mapping stale if freshness policy exceeded. Health status must not auto-approve discovered items. Protocol incompatibility returns typed `MCP_UNSUPPORTED_PROTOCOL`; invalid schema hash returns `MCP_MAPPING_STALE`.

## 9. Test matrix / acceptance evidence

| ID | Test | Acceptance |
|---|---|---|
| MCP-01 | Register mock remote MCP | secure connection with secret_ref only |
| MCP-02 | tools/list tool appears | discovered but not invokable |
| MCP-03 | Admin approves mapping | exact ToolVersion and hash pinned |
| MCP-04 | Schema drift after approval | invoke blocked, review required |
| MCP-05 | Cross-tenant connection access | denied DB/API/runtime |
| MCP-06 | Unsafe host/redirect DNS | SSRF blocked |
| MCP-07 | MCP write timeout | `unknown`, no blind replay |
| MCP-08 | Revoked connection/tool grant | fail closed even for published Agent |
| MCP-09 | Protocol version 2026-07-28 | stateless request/response path works |
| MCP-10 | Legacy version | adapter works only if explicitly enabled/tested |
| MCP-11 | Malicious tool description | cannot inject system policy or leak credentials |

## 10. Kế hoạch Pull Requests

Execute **MCP-PR01 → MCP-PR08** from `references/CR-MCP-001.md`. Core steps contracts/SDK → migration/RLS → connection API → discovery/schema diff → approval/registry → runtime/gateway → UI → security/E2E. Do **not** start MCP publish/server phase as incidental extension.

## 11. Observability / metrics / operations

Discovery duration/success, connection health, schema drift rate, approved/revoked mapping changes, tool latency/errors, SSRF blocks, mismatched schema denies, remote outage reconciliation. Logs show connection IDs/tool version/trace IDs, no credentials or tool argument contents.


## Cross-cutting rules (bắt buộc)

- **Source of truth:** `docs/00-governance/CANONICAL_DATABASE_56.md` và 50-table `references/ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql`; 3 bảng Skill và 3 bảng MCP là migration mở rộng, **chưa xác nhận chạy thật**. Tổng 56 domain tables là TARGET, không phải production claim. Các bảng framework/checkpointer nội bộ nếu có phải kiểm kê và cách ly, không giả vờ nằm trong 56.
- **Tenant boundary:** derive `tenant_id` từ authenticated principal; xác minh `workspace_id`, ACL, RLS (khi tồn tại) bằng DB role không `BYPASSRLS`; không tin ID hoặc claim do model cung cấp. Object storage, cache, Qdrant, event streaming phải áp cùng scope.
- **Immutability:** AgentVersion, WorkflowVersion, SkillVersion, ToolVersion, eval dataset versions đã publish là immutable; run pin exact version. Nếu phải thêm cột/index/bảng mới, viết ADR + migration review; **không tự tạo bảng domain thứ 57**.
- **Authority:** LLM/Skill/RAG/MCP output là dữ liệu không đáng tin; không được tự tạo grants. Tool side effects bắt buộc đi qua `SPEC-TOOL-001` / policy `SPEC-SECURITY-001`; HITL qua `SPEC-WORKFLOW-001`.
- **Evidence:** code/DDL/documentation khác với triển khai chạy thật. Từng PR phải báo `PASS` / `FAIL` / `BLOCKED` / `NOT_RUN` cùng câu lệnh test và bằng chứng; không được ghi PASS chỉ vì đã viết mock hoặc file spec.
- **No duplicate services:** `control-api` là modular monolith, `agent-runtime` là execution plane; `studio-web` là UI. Tái sử dụng các module, contracts, migration tooling đang tồn tại trước khi tạo tên thư mục mới.

## Uniform coding-agent delivery contract

Mỗi PR phải giao: thay đổi file (paths thực tế), contracts thay đổi, migration impact (hoặc `none`), các kiểm thử với output, backward compatibility, security findings, rollback/recovery và danh sách TODO chưa xong. Nếu repo local khác layout đề xuất thì dùng module tương đương và ghi mapping; không tạo song song. Thực hiện một PR/phạm vi mỗi lượt; trạng thái dự án được kiểm tra **read-only** trước khi sửa.

## Technical references

- https://modelcontextprotocol.io/specification/2026-07-28
- https://blog.modelcontextprotocol.io/posts/2026-07-28/
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx
