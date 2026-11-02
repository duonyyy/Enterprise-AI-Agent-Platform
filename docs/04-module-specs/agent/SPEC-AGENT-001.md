# SPEC-AGENT-001 — Agent Registry, Lifecycle & Declarative AgentSpec

**Status:** DESIGN READY FOR CODE REVIEW; NOT IMPLEMENTED · **Database:** `DB-BASELINE-56` · **Priority:** P0 · **Owner:** Control API

## 1. Goal, boundaries, dependencies

Xây Agent Registry cho Enterprise AI Agent Platform: tạo draft, cập nhật có optimistic concurrency, validate dependency, publish **immutable AgentVersion**, promote/rollback environment bằng pointer đến version. Agent Studio chỉ sinh cấu hình; không sinh và thực thi Python tùy ý từ người dùng.

Phụ thuộc: Identity/Workspace, database RLS, Tool Registry, Model Registry, contracts. Tích hợp dần CR-SKILL-001, CR-MCP-001; **chưa cần** Knowledge/RAG hoặc visual Workflow để hoàn thành vertical slice đầu.

Không làm: tự động tạo Agent có quyền cao; tự duyệt Skills/Tools; viết Agent Runtime mới; thêm bảng ngoài 56; xem UI là nguồn quyền.

## 2. Database mapping — sử dụng đúng baseline 56

| Bảng | Trách nhiệm |
|---|---|
| `catalog.agents` | Draft mutable với `revision`, `draft_spec`, `slug`, `archived_at` |
| `catalog.agent_versions` | Frozen spec + bundle SHA + `workflow_version_id`, `model_config_id` |
| `catalog.agent_publications` | 1 active version pointer theo `(tenant,workspace,agent,environment)` + revision |
| `catalog.agent_tool_bindings` | Agent version → tool version, `usage_mode` |
| `catalog.agent_knowledge_bindings` | Agent version → knowledge base |
| `catalog.agent_skill_bindings` | **Bảng 56 mới**: Agent version → pinned SkillVersion |
| `catalog.skill_versions`, `skills` | Kiểm tra active/published, hash & quyền |
| `catalog.tool_versions`, `tools` | Bind tool version, không dùng “latest” |
| `integration.mcp_tool_mappings` | Nếu tool kind=mcp: duyệt `approved`, hash chưa stale/revoked |
| `catalog.model_configs`, `workflow_versions` | References đã được cho phép |
| `governance.audit_events`, `integration.outbox_events` | Audit + publish event trong transaction |

Không tạo `agent_registry`, `agent_dependencies`, `agent_mcp_bindings` mới. Nếu thiếu một tính năng, mở ADR; không tự ý mở rộng DB.

## 3. AgentSpec v1 — hợp đồng đầu vào và serialization

Ví dụ **minh họa**; Coding Agent phải viết JSON Schema thực tế và test validator.

```json
{
  "schemaVersion": "1.0",
  "name": "QuotationAssistant",
  "mode": "fixed_plan",
  "modelRef": "approved-model-id",
  "persona": {"instructions": "Draft quotation only; request approval before external actions."},
  "toolRefs": ["tool-version-id-1"],
  "skillRefs": ["skill-version-id-1"],
  "knowledgeRefs": [],
  "workflowRef": null,
  "limits": {"maxSteps": 8, "maxToolCalls": 12, "maxOutputTokens": 2048}
}
```

`modelRef`, `toolRefs`, `skillRefs`, `workflowRef` là references được resolve trên server từ `(tenant_id,workspace_id)` và **đúng version ID**. `schemaVersion` quản lý contract, khác integer `agent_versions.version`; `name` không là ID authoritative. `persona` được kiểm tra không chứa secrets. `mode` chỉ những chế độ compiler/runtime hỗ trợ; MVP dùng `fixed_plan` hoặc `react_bounded` sau khi implement.

`contracts/jsonschema/agent-spec.v1.schema.json` là source of truth; Pydantic models chỉ mirror contract; generated TypeScript types do codegen tạo. Không copy mỗi nơi một schema khác nhau.

## 4. State machine & domain invariants

Draft Agent: `active`, `archived` qua `archived_at`; draft update bằng `revision` CAS. Published version immutable. Publication pointer theo environment: create, promote, rollback pointer `agent_publications.agent_version_id`. **Rollback = chuyển pointer đến version cũ**; không sửa nội dung version cũ.

**Invariants:**

1. Agent có thể có 0 Skill; thay đổi không bắt buộc backfill Agent cũ.
2. Mọi Skill/Tool/Knowledge thuộc đúng workspace, đúng quyền; không bind chéo tenant; không bind version revoked/disabled.
3. Agent version pin đầy đủ refs và hashes; bundle hash deterministic (canonical serialization) bao gồm dependency lock manifest, không phụ thuộc thứ tự key JSON.
4. `create draft` không gây side effect; `publish` là controlled operation; `run` là trách nhiệm Runtime.
5. Published version và bindings không UPDATE/DELETE; chỉ thêm version và thay publication pointer.
6. Nếu MCP schema drift hoặc Skill bị disable **sau publish**, runtime re-check chính sách và fail-closed khi invoke/load.
7. Không đưa credential vào spec hoặc response; chỉ server-side `secret_ref`.
8. Idempotency key: cùng key + cùng canonical request hash trả kết quả tương đương; cùng key + khác hash trả `409`.

## 5. Control API endpoint contract

Prefix `/api/v1/workspaces/{workspace_id}`; **tenant lấy từ verified principal**, không nhận tenant tự do từ body.

| Verb + endpoint | Permission | Tác dụng và status |
|---|---|---|
| `POST /agents` | `agent:create` | `201` + draft id/revision |
| `GET /agents` | `agent:read` | `200` + cursor pagination |
| `GET /agents/{agent_id}` | `agent:read` | `200` + draft and versions metadata |
| `PATCH /agents/{agent_id}` | `agent:edit` | `200`; bắt buộc `If-Match` revision, sai `409/412` |
| `POST /agents/{agent_id}/validate` | `agent:edit` | `200` validation diagnostics; không publish |
| `POST /agents/{agent_id}/publish` | `agent:publish` | `201` immutable version; `Idempotency-Key` |
| `GET /agents/{agent_id}/versions` | `agent:read` | `200` versions list |
| `POST /agents/{agent_id}/promotions` | `agent:promote` | `200`; update publication pointer, audit |
| `GET /agents/{agent_id}/publications` | `agent:read` | `200` environment active versions |

Errors: `AUTH_REQUIRED`=401, `FORBIDDEN`=403, `NOT_FOUND`=404 (mask cross-tenant), `INVALID_SPEC`=422, `STALE_REVISION`=409/412, `DEPENDENCY_REVOKED`=409, `IDEMPOTENCY_CONFLICT`=409, `INTERNAL`=500. Response envelope có `requestId`/`traceId`, không leak dữ liệu nhạy cảm.

## 6. Required files — delta only

```text
contracts/jsonschema/agent-spec.v1.schema.json
contracts/openapi/control-api.openapi.yaml
apps/control-api/src/control_api/modules/agents/
  api/{router.py,requests.py,responses.py}
  application/{create_agent.py,update_agent.py,validate_agent.py,publish_agent.py,promote_agent.py}
  domain/{entities.py,rules.py,events.py}
  infrastructure/{repository.py,orm_models.py}
  tests/{test_draft.py,test_publish.py,test_versions.py,test_authz.py}
apps/studio-web/src/features/agent-builder/   # API client later; no UI rewrite in this spec
```

File locations are **proposed**, inspect actual repo first. `main.py` chỉ include router, không chứa nghiệp vụ. `repository.py` không tự quyết định policy; use cases gọi Authz service trước khi ghi.

## 7. Publish transaction & concurrency

1. Verify caller membership and `agent:publish`; resolve tenant/workspace from principal.
2. Lock agent row `SELECT ... FOR UPDATE`, kiểm tra revision, validate canonical spec và dependency versions.
3. Check active status of Skill, Tool, MCP mapping, model and workflow; evaluate policy.
4. Freeze dependency list with pinned hashes; canonicalize spec/bundle to digest.
5. Insert one `catalog.agent_versions`, then immutable `agent_tool_bindings`, `agent_skill_bindings`, `agent_knowledge_bindings`.
6. If promotion requested, atomically update publication pointer with expected revision; stage-only publish without promotion otherwise.
7. Write redacted audit + outbox message **in same transaction**; return idempotent response only after COMMIT.
8. Retried publish same idempotency key must not allocate new version. The 56-table baseline has no dedicated generic idempotency response table; for this operation **use stable deduplication correlation backed by existing outbox/version unique keys or an explicit ADR before implementation**, rather than claim a guarantee from in-memory cache.

Test concurrent publish 10+ requests, unique `(tenant,workspace,agent,version)`, no partially created bindings, and linearizable revision increment under row lock.

## 8. Security & quality gates

- Authz checked before database reads; RLS additionally enforced on application role, never migration/superuser.
- Workspace-level membership and role enforcement; tenant RLS is necessary but not sufficient.
- Cross-tenant IDs return 404 or controlled denial without leaking resource presence.
- Immutable versions cannot be modified even by ordinary application DB role.
- Published MCP binding cannot refer to `pending`, `stale`, `revoked` mappings.
- Disabled Skill prevents new loads; existing run policy behavior documented and tested.
- `GET /versions` may return metadata, not secret refs or unredacted instructions unless privileged.
- Negative tests: stale `If-Match`, invalid JSON Schema, non-existent skill/tool versions, disabled connection, wrong workspace, concurrent publish, UUID mix-up.

## 9. PR plan / acceptance

| PR | Changes | Evidence |
|---|---|---|
| `AG-PR01` | AgentSpec JSON Schema + examples + contract tests | Valid/invalid fixtures; TS/Python generated type parity |
| `AG-PR02` | Draft CRUD + role/tenant authz + revision | Negative tenant tests, 409/412 concurrency |
| `AG-PR03` | Publish atomic + pinned dependency bindings + audit | DB transaction test, immutable trigger, outbox |
| `AG-PR04` | Publication pointers (dev/staging/prod), rollback | Concurrent promote/retry test |
| `AG-PR05` | Integrate CR-SKILL-001 and CR-MCP-001 gates when modules ready | Skill/MCP revocation and schema drift tests |

**Exit gate:** A validated draft becomes one immutable version; new run resolves exact version; no unauthorized publish/cross-tenant binding; migration counts stay within 56. Document `NOT_RUN` where evidence missing.

## 10. Required reference docs

- `docs/00-governance/CANONICAL_DATABASE_56.md` (new, authoritative target)
- `CR-SKILL-001`, `CR-MCP-001` (existing change requests)
- `ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql` (50 core DDL)
- Agno registry design: https://docs.agno.com/agent-os/studio/registry
- FastAPI modular routers: https://fastapi.tiangolo.com/tutorial/bigger-applications/
