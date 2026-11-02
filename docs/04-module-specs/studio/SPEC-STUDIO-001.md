# SPEC-STUDIO-001 — Agent Studio, Visual Builder & Operations Console
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P1 · **Owner:** Next.js studio-web UI + Control API contracts

## 1. Mục tiêu, phụ thuộc và ranh giới

Xây giao diện self-service doanh nghiệp không chứa logic cấp quyền: workspace switch, Agent draft editor/version diff/publish, Skill/MCP catalog, Tool grants, Knowledge, Workflow Builder, Run Inspector, Approval Inbox và Eval dashboards. UI đọc contracts OpenAPI generated; không gọi thẳng PostgreSQL, Qdrant, remote MCP hay provider LLM. Thứ tự hoàn thiện theo API thực tế, cho phép feature flag những phần chưa có backend.

**Phụ thuộc:** Agent/Tool/Security specs P0; Skills/MCP CR; Workflow/Knowledge/Evals APIs as implemented

**Không nằm trong scope:** UI-created Python/JS nodes; frontend-only RBAC; a second frontend server managing its own business DB; feature flags that claim unfinished functionality as completed; bypassing backend through direct vendor/MCP calls.

## 2. Mapping sang CSDL 56 bảng

UI **không sở hữu bảng**; mapping thao tác đến bảng nguồn:

| UI feature | API/domain owner | Tables indirectly affected |
|---|---|---|
| Agent list/builder/versions | Agent Registry | `catalog.agents`, `agent_versions`, `agent_publications` |
| Skill picker/editor | Skills service | `catalog.skills`, `skill_versions`, `agent_skill_bindings` |
| MCP connections/discovery | MCP service | `integration.connections`, `mcp_*`; tool grants from Tool Registry |
| Knowledge Explorer | Knowledge service | `knowledge.bases/documents/...` |
| Workflow Canvas | Workflow service | `catalog.workflows`, `workflow_versions` |
| Run Inspector | Runtime | `runtime.runs`, `run_events`, `run_steps` |
| Approval Inbox | Approval service | `runtime.approval_requests`, `approval_decisions` |
| Eval dashboard | Evaluations | `ops.eval_*`, `ops.model_usage` |

No UI-specific domain tables (57+) for layout or preferences without ADR; ephemeral editor state may be local until publish.

## 3. Luồng hoạt động và domain invariants

1. Authenticated workspace route resolves current membership and permissions from Control API; UI hides unavailable actions but backend remains authoritative.
2. Agent Editor uses canonical generated DTO + JSON Schema validation; draft save with ETag/`If-Match`; two browser tabs conflict UI offers reload/diff (never blind overwrite).
3. Configure capabilities by selecting **approved pinned versions**: Tool/Skill/Knowledge/Model/Workflow; show permission warnings before publish. MCP tool schema drift shows stale status and blocks save/publish unless fixed.
4. Validate → display errors linked to fields → publish → immutable version card; promotion requires role and gate conditions; rollback changes pointer not version bytes.
5. Workflow visual graph is a projection/editing of canonical WorkflowSpec, not a separate graph engine. Validate edges and bounded conditions both frontend/backend; generated JS cannot become executable graph node.
6. Run Inspector follows SSE `run_events` cursor/sequence, reconnect with last event ID, shows waiting approvals and tool receipts; event list may contain redacted fields only.
7. Approval Inbox shows canonical action preview, expiry and policy; Approve/Reject request attaches current revision and CSRF/CSRF-equivalent token; server checks identity and decision.
8. UI handles loading, stale, forbidden, empty, revoked, aborted runs; no false visual `COMPLETED` until terminal run status is confirmed from server.
9. Accessibility WCAG-based keyboard navigation and responsive layouts; audit destructive actions and permission failures.

Demo slice: create Agent draft→select Skill + mock Tool→publish→run→approve→resume→inspect trace/results.

## 4. API contracts & authorization

UI consumes existing API contracts from `SPEC-AGENT-001`, `SPEC-RUNTIME-001`, `SPEC-TOOL-001`, `SPEC-WORKFLOW-001`, `CR-SKILL-001`, `CR-MCP-001`; no new backend route solely to mask missing implementation. Proposed UI views: `/w/[workspaceId]/agents`, `/agents/[agentId]`, `/skills`, `/mcp`, `/tools`, `/knowledge`, `/workflows`, `/runs/[runId]`, `/approvals`, `/evaluations`, `/settings` (adapt to current routing). All data fetches carry authenticated session; permissions are derived from backend.

Required UI contract errors: 401 redirect/auth, 403 permission state, 404 masked resource, 409 conflict, 422 field validation, 429 backoff, 5xx retry-safe feedback. Server actions that mutate need typed idempotency key where backend requires.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

Client data models generated from approved OpenAPI/JSON Schema; no duplicated hand-maintained types. Forms must distinguish `draftRevision`, `publishedVersionId`, `publishedAt`, `targetEnvironment`, `pendingApprovalId` and `runStatus` to avoid mixing transitions. Each view has `loading|ready|empty|error|forbidden|stale` states. Workflow Canvas stores canonical JSON plus client viewport metadata **only in client state** unless separate product decision.

Contract testing validates generated client compatibility against server OpenAPI. Use design tokens/accessible component primitives; human-visible errors contain safe requestId but no secrets.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
apps/studio-web/src/app/(console)/w/[workspaceId]/{agents,skills,mcp,tools,knowledge,workflows,runs,approvals,evaluations,settings}/
apps/studio-web/src/features/{agent-builder,skill-management,mcp-management,tool-registry,knowledge,workflow-builder,run-monitor,approvals,evaluations}/
apps/studio-web/src/shared/{api/generated,auth,permissions,components,errors,telemetry}/
contracts/openapi/control-api.openapi.yaml
tests/{ui/accessibility,ui/contract,e2e/agent_publish_run_approval.spec.ts,e2e/tenant_isolation.spec.ts}
```

## 7. Security, privacy và kiểm soát quyền

No secrets in form state, localStorage, query strings, SSR logs or telemetry. Use httpOnly session cookies / authenticated proxy policy as current implementation; protect CSRF for writes. XSS-safe rendering for Markdown, Skill instructions, RAG chunks and tool output; sanitize links and avoid HTML execution. Cross-tenant workspace-switch must clear caches and SSE channels. Browser never holds remote MCP tokens or service credentials. UI permissions are informative only; backend authorization mandatory.

## 8. Lỗi, retry, idempotency và recovery

Stale revision: explicit conflict UI, no client auto-merge of semantic AgentSpec/Workflow graph. SSE disconnect: resume by event ID and server cursor, dedup sequence; on 410/historical gap refetch current run and indicate missing events. Optimistic approval button must not show success until response/updated state. Retry network mutations only with idempotency key and same body; fallback to read-only state if server unavailable.

## 9. Test matrix / acceptance evidence

| ID | Scenario | Pass condition |
|---|---|---|
| UI-01 | Agent draft/publish | references pinned version; versions immutable |
| UI-02 | Two-tab edit conflict | 409/412 shown; no silent overwrite |
| UI-03 | Tenant/workspace switch | no stale private data in UI caches/SSE |
| UI-04 | Unauthorized user | blocked on backend even with manually triggered fetch |
| UI-05 | Skill/MCP stale/deleted | disabled/visible error; no invalid publish |
| UI-06 | Run SSE reconnect | no duplicate/out-of-order event display |
| UI-07 | Approver rejects | no tool invocation; UI reflects terminal semantics |
| UI-08 | Script/HTML in Skill/RAG text | sanitized; no XSS |
| UI-09 | Keyboard accessibility | focus/labels/errors usable without mouse |
| UI-10 | Backend API changes | generated-client contract CI fails before deploy |

## 10. Kế hoạch Pull Requests

- **UI-PR01:** design system, route/auth/workspace, generated client, permission states.
- **UI-PR02:** Agent List/Builder/versions/publish + validation.
- **UI-PR03:** Skill/MCP/Tool pickers and management (only after respective APIs).
- **UI-PR04:** Workflow editor and Knowledge Explorer.
- **UI-PR05:** Run Inspector/Approvals with SSE recovery.
- **UI-PR06:** Evaluations/Operations dashboards, accessibility and E2E.

## 11. Observability / metrics / operations

Front-end error rates, page interaction latency, API requestId correlation, publish success/failure, SSE reconnect counts, approval action latency, accessibility violations in CI. No logging of full form values, prompts, credentials, tool arguments or document excerpts.


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

- https://docs.agno.com/
- https://opentelemetry.io/docs/specs/semconv/
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
