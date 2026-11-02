# SPEC-LLM-001 — Model Gateway, Provider Routing, Budgets & Structured Outputs
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P1 (core adapter P0) · **Owner:** Control API model registry + Runtime model gateway

## 1. Mục tiêu, phụ thuộc và ranh giới

Xây một model-provider abstraction thống nhất cho chat/tool calling, structured output và embeddings theo capabilities; model config có secrets manager ref và workspace quota. Hỗ trợ provider routing có kiểm soát, budget, tracing và retry. MVP chọn **một provider thật + deterministic fake provider**; provider thứ hai chỉ triển khai sau contract tests để không làm trễ vertical slice.

**Phụ thuộc:** `SPEC-SECURITY-001`, `SPEC-RUNTIME-001`, `SPEC-AGENT-001`

**Không nằm trong scope:** Replacing LangGraph, auto-selecting unapproved providers, arbitrarily using customer's model key in browser, silently paying for provider fallbacks, unmetered experiments, pretending local token estimates are actual billable usage.

## 2. Mapping sang CSDL 56 bảng

| Bảng | Trách nhiệm |
|---|---|
| `catalog.model_configs` | approved provider, `provider_model_id`, `settings`, `secret_ref`, `status`; **không có bảng model_registry mới** |
| `catalog.agent_versions` | `model_config_id` pinned tại publish |
| `ops.model_usage` | usage provider-reported vs estimated, `pricing_version`, cost USD |
| `runtime.run_steps`, `run_events` | model call attempts, timeouts, trace metadata |
| `governance.policy_sets`, `policy_versions` | model allowlist, quota/risk policies |
| `governance.audit_events` | config/grant changes and denial reasons |

`model_configs` **không có** bảng riêng về model capability catalogue/routing rules. Khởi đầu dùng validated `settings`/policy config với immutable digest; nếu cần trạng thái cập nhật, không thêm bảng tự động.

## 3. Luồng hoạt động và domain invariants

1. Control API register model config from approved provider identifiers; validate settings against allowlist (`temperature`, `maxOutputTokens`, etc.), no arbitrary base URL unless SSRF-reviewed.
2. At publish resolve `modelRef` to workspace-scoped approved model config; verify capability compatibility for schema/tool calls/embeddings; capture config digest in AgentVersion.
3. At runtime assemble `ModelRequest` with priority messages, approved tool defs, structured output contract, deadline and *remaining* budget. Gateway derives service credential from `secret_ref`, never accepts raw provider key from AgentSpec/UI.
4. Call `generate()` through provider adapter; normalize model outputs, tool call IDs, token usage, safety/refusal, finish reason. Provider output is untrusted until schema validated.
5. Check per-run/request quotas before call, update actual spend from response, with atomic budget reservation accounting in app/Redis only if correctness verified. Use `ops.model_usage` durable reporting; do not equate billing estimates with vendor invoice.
6. Retry only transient cases and within global deadline/turn cap. Fallback to another provider requires **explicit compatibility and policy permission**, plus new trace; no silent fallbacks for tasks requiring deterministic behavior or region-bound data.
7. Streaming chunks are ephemeral UI data, final model result and measured usage captured once per response. On disconnect decide whether in-flight provider call is cancelable and how usage is measured; no unbounded retry.
8. Model-generated tool calls are intents; `SPEC-TOOL-001` validates before side effect; model cannot choose arbitrary registered tool outside bound allowlist.

## 4. API contracts & authorization

| Method | Path | Permission | Notes |
|---|---|---|---|
| `POST` | `/models` | `model:manage` | create config metadata + secret_ref |
| `GET` | `/models` | `model:read` | no credential values |
| `PATCH` | `/models/{model_config_id}` | `model:manage` | restrict mutable settings; published AgentVersion semantics |
| `POST` | `/models/{model_config_id}/validate` | `model:manage` | bounded safe no-secret diagnostic |
| `GET` | `/models/{id}/usage` | `usage:read` | workspace summary from `ops.model_usage` |

Do not expose raw model invocation endpoint on Control API that bypasses Agent run authorization; playground should invoke a published/draft test session through scoped Runtime path.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

Canonical adapter interface (Python): `generate(ModelRequest)->ModelResponse`, `stream(ModelRequest)->AsyncIterator[ModelDelta]`, `embed(EmbeddingRequest)->EmbeddingResponse` (only when supported). Normalized errors: `MODEL_AUTH_FAILED`, `MODEL_RATE_LIMITED`, `MODEL_TIMEOUT`, `MODEL_UNAVAILABLE`, `MODEL_CONTEXT_TOO_LARGE`, `MODEL_INVALID_RESPONSE`, `MODEL_POLICY_DENIED`, `MODEL_BUDGET_EXCEEDED`.

ModelRequest fields: `modelConfigId`, `runId`, `messages[]`, `tools[]`, `responseSchema?`, `deadlineAt`, `maxOutputTokens`, `remainingCostUsd?`, `idempotencyContext?`. Output: `message`, `toolCalls[]`, `usage:{inputTokens,outputTokens,usageSource}`, `providerRequestId?`, `finishReason`, `actualModelId`. Cost calculation needs immutable pricing snapshot/version; if missing mark **estimated/unknown** rather than fabricate.

Capabilities are explicit (`supportsTools`, `supportsJsonSchema`, `supportsStream`, `supportsEmbeddings`) and checked at validation; never assume provider API compatibility because method names match.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
apps/control-api/src/control_api/modules/models/{router.py,schemas.py,service.py,repository.py,policy.py}
apps/agent-runtime/src/agent_runtime/llm/{gateway.py,provider_protocol.py,providers/__init__.py,providers/fake.py,providers/primary.py,capabilities.py,usage_meter.py,budget.py,errors.py}
contracts/jsonschema/{model-request.v1.schema.json,model-response.v1.schema.json}
tests/{unit/llm,integration/model_gateway,contract/model_providers,e2e/model_budget.py}
```

## 7. Security, privacy và kiểm soát quyền

Secret refs resolved server side with workspace binding; encrypted transport to provider. Sanitize URL and proxy configuration. Prevent prompt injection from overriding model routing, data residency or cost cap. Sensitive inputs/outputs not recorded in traces by default. Tenant-specific vendor key/cost/limits cannot be read by other tenants. Require explicit approval of third-party data processing location and provider retention terms before use with customer records.

## 8. Lỗi, retry, idempotency và recovery

Classify 429/503 retryable with jitter, never infinite retry; 401 invalid secret is terminal until admin fixes; malformed structured output may use bounded repair attempts that count toward budget; streamed response partially delivered must not be labeled complete. Provider timeout after model finished may mean billable unknown usage; mark measured/estimated status and reconcile if provider offers API. Circuit breaker is optional after metrics evidence; never globally open circuit across tenants without justification.

## 9. Test matrix / acceptance evidence

| ID | Test | Acceptance |
|---|---|---|
| LLM-01 | Fake provider normal message | typed normalized output, schema passes |
| LLM-02 | Tool-call suggestion outside grants | cannot call tool; gateway denial |
| LLM-03 | Provider timeout/rate limit | bounded retry and error code, budget enforced |
| LLM-04 | Invalid JSON structured output | validated failure or bounded repair, no invalid state write |
| LLM-05 | Max steps/tokens/cost | requests stop at hard limits |
| LLM-06 | Per-tenant credentials | no key leakage via API/log/trace/cache |
| LLM-07 | Model config disabled after publish | runtime re-evaluates policy, fail closed |
| LLM-08 | Unsupported tool/JSON capability | validation error before execution |
| LLM-09 | Usage missing from provider | record source=`estimated`/unknown per SQL allowed values; no fake exact cost |

## 10. Kế hoạch Pull Requests

- **LLM-PR01:** adapter protocol, normalized contracts, deterministic fake, capability tests.
- **LLM-PR02:** Model Registry API and secret_ref + tenant policy.
- **LLM-PR03:** first provider integration, streaming/structured output.
- **LLM-PR04:** quotas, usage persistence, retry/fallback policy + telemetry.
- **LLM-PR05:** provider contract suite, cost and security E2E.

## 11. Observability / metrics / operations

model call p50/p95, token usage, output length, vendor error class, rate-limit hits, budget denial, schema-validation failures, cost by workspace/model, fallback reason. Use trace IDs without messages by default, avoid high-cardinality raw prompt tags.


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

- https://docs.agno.com/agents/overview
- https://opentelemetry.io/docs/specs/semconv/
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
