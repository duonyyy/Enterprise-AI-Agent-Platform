# SPEC-MEMORY-001 — Agent Memory, Conversation & Context Engineering
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P1 · **Owner:** Agent Runtime + Control API privacy/consent

## 1. Mục tiêu, phụ thuộc và ranh giới

Thiết kế ba ranh giới KHÁC NHAU: (a) conversation history để hiển thị, (b) LangGraph checkpoint thread để resume, (c) consented long-term memory có scope và TTL. Context builder kiểm soát token budget, thứ tự thông điệp, nguồn tin cậy và compaction. Long-term memory MVP phải opt-in, có quyền xóa và không được dùng để cấp quyền.

**Phụ thuộc:** `SPEC-RUNTIME-001`, `SPEC-SECURITY-001`, `SPEC-LLM-001`, `SPEC-KNOWLEDGE-001` for retrieval only

**Không nằm trong scope:** Treating checkpoint, chat transcript and long-term memory as one table; auto-persisting all messages as user profile; autonomous memory write granting new tools; unlimited context history; using public internet to enrich private memory without user consent.

## 2. Mapping sang CSDL 56 bảng

| Bảng | Ý nghĩa |
|---|---|
| `runtime.conversations` | identity của cuộc hội thoại và `external_subject` đã kiểm soát scope |
| `runtime.messages` | message metadata + `body_ref`/`body_ciphertext`; tránh plaintext trong logs |
| `runtime.checkpoint_refs` | pointer `thread_id/checkpoint_id`; không thay thế backend durable checkpointer |
| `runtime.memory_records` | `subject_key`, `memory_kind`, `ciphertext`, `consent_ref`, `valid_until` |
| `runtime.runs`, `run_events` | run state, provenance và references cho assembled context |
| `governance.retention_policies` | deletion, expiry, legal hold policy |
| `governance.audit_events` | memory read/write/delete access log (metadata only) |

**Không tạo** `agent_memories`, `session_summaries`, `memory_vectors` DB tables. If semantic retrieval of long-term memory required, write ADR and store in authorized Qdrant payload/`memory_records` using controlled design; MVP deterministic indexed DB lookup by subject/scope.

## 3. Luồng hoạt động và domain invariants

1. `conversation_id` resolved and authorized by verified principal; never trust user-provided `external_subject` to override identity.
2. Runtime reads pinned agent spec and thread checkpoint via durable saver with scoped `thread_id`; transcript from `messages` is for UI/history, not the source of truth for resumed node state.
3. Context builder orders: trusted system/policies → published AgentSpec/persona → approved tool schemas/Skill **metadata** → recent scoped conversation → authorized summaries/memory → RAG excerpts as *quoted untrusted data*. User messages cannot impersonate higher-priority roles.
4. Estimate input token budget using model-aware tokenizer or conservative estimator; reserve output/tools/safety margins. Hard stop/rebuild context when over budget; never silently truncate system rules or approval hashes.
5. Compaction policy: retain recency window, preserve unresolved user constraints and pending actions; summarize older messages with provenance and timestamps; mark summaries as derived, subject to retention/consent.
6. Long-term capture: explicit consent policy; redact secrets/PII by class; store ciphertext/`consent_ref`, `valid_until`; subject ownership/permissions required on read/update/delete.
7. On forget/retention, tombstone/cryptographic erasure as feasible, purge caches/secondary indexes asynchronously, document backup lifecycle limits.
8. Approval and Tool Gateway evaluate **current** grants, never treat remembered advice as authorization. Prompt-injected memory is lower-trust data; require source/provenance and constraints.

## 4. API contracts & authorization

| Verb | Path (workspace scoped) | Permission | Intent |
|---|---|---|---|
| `GET` | `/conversations` | `conversation:read` | list authorized conversations |
| `GET` | `/conversations/{conversation_id}/messages` | `conversation:read` | decrypted response only to allowed subject |
| `POST` | `/conversations/{conversation_id}/messages` | `conversation:write` | append user message + idempotency |
| `GET` | `/memory` | `memory:read` | list own scoped entries / safe metadata |
| `POST` | `/memory` | `memory:write` | requires consent and validation |
| `DELETE` | `/memory/{memory_id}` | `memory:delete` | revoke / async downstream deletion |
| `POST` | `/memory/preview-context` | `memory:inspect` | redacted token breakdown, no secrets |

Actual `memory_records` lacks update/delete tracking columns; deletion semantics must be ADR-reviewed vs existing schema and retention obligations.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

`ContextSegment`: `{kind, sourceRef, trustLevel, approxTokens, insertedAt, redacted}`. `ContextBuildResult`: `{segments, totalTokens, reservedOutputTokens, truncatedSegments, provenanceDigest}`; input text itself not logged. `MemoryRecordInput`: `{subjectKey, memoryKind, content, consentRef, validUntil}` validated server side, encrypted at rest. Hard limits on records, payload size, TTL. Model switch must re-evaluate context budget; stale token count cannot authorize overflow.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
apps/agent-runtime/src/agent_runtime/context/{builder.py,trust_boundary.py,token_budget.py,compactor.py}
apps/agent-runtime/src/agent_runtime/memory/{conversation_reader.py,short_term.py,long_term.py,consent_policy.py}
apps/control-api/src/control_api/modules/memory/{router.py,schemas.py,service.py,repository.py}
apps/studio-web/src/features/conversations/{HistoryPanel.tsx,MemorySettings.tsx}
contracts/jsonschema/{context-segment.v1.schema.json,memory-request.v1.schema.json}
tests/{unit/context,integration/memory,e2e/memory_consent_delete.py}
```

## 7. Security, privacy và kiểm soát quyền

Encrypt content with managed KMS envelope or equivalent, never hardcode keys. Honor tenant/workspace/subject in every query. Prevent model/user injection into trust hierarchy. Strip secrets and adversarial instructions from stored summaries; place retrieved memory behind clear lower-trust delimiter. Avoid embedding customer data into an external provider without approved data processing rules. Include per-tenant retention, consent and discovery/forget APIs. Account for enterprise/legal hold without pretending immediate deletion of immutable backups.

## 8. Lỗi, retry, idempotency và recovery

Crash after message DB commit but before checkpoint: reconcile via run/checkpoint event references; do not blindly regenerate model/tool side effects. Duplicate message idempotency key: safe replay, reject changed payload. Unavailable memory store: fail closed for permission checks and explicitly degrade personalized context if approved; never leak other tenant cache. Compaction failure: drop optional lower-priority context or surface typed `CONTEXT_BUDGET_EXCEEDED`, not truncate hidden policy.

## 9. Test matrix / acceptance evidence

| ID | Test | Acceptance |
|---|---|---|
| MEM-01 | Same conversation restart | checkpoint state resumable; transcript consistent but independent |
| MEM-02 | Tenant A reading B's memory | deny, real RLS and cache isolation |
| MEM-03 | Opt-out long-term memory | zero persistent long-term writes |
| MEM-04 | TTL expired | not returned; scheduled clean-up auditable |
| MEM-05 | Oversized prompt | no truncation of system/approval; bounded error |
| MEM-06 | Poisoned memory instructs unsafe tool | gateway denies; trace records reason |
| MEM-07 | Delete consented memory | DB unreadable, cache invalidated, purge receipt |
| MEM-08 | Change model context length | token budget recalculated and enforced |
| MEM-09 | Parallel memory update | conflict/correct ordering; no cross-subject corruption |

## 10. Kế hoạch Pull Requests

- **MEM-PR01:** context-segment schema and budget/priority tests.
- **MEM-PR02:** conversation store/read path and restart semantics.
- **MEM-PR03:** long-term memory API, subject ACL, encryption, consent.
- **MEM-PR04:** compaction, TTL/delete workflows, privacy test suite.
- **MEM-PR05:** Studio memory controls and end-to-end tenant tests.

## 11. Observability / metrics / operations

Context utilization, truncation rate by type, compaction latency/cost, memory fetch errors, TTL cleanup lag, cross-tenant denied reads, consent coverage; no raw memory values in logs. Trace context assembly counts and source refs only.


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

- https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx
- https://docs.agno.com/
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
