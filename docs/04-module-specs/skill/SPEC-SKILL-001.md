# SPEC-SKILL-001 — Skill Management, Progressive Discovery & Agent Binding
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P1 (optional early library) · **Owner:** Control API Skills + Agent Runtime Skill loader

## 1. Mục tiêu, phụ thuộc và ranh giới

Chuẩn hóa Skill Management thành module spec độc lập, **không viết lại** `CR-SKILL-001`. CR là chi tiết hợp đồng quản lý file `SKILL.md`, import/validate/publish, progressive disclosure, audit; SPEC này là bản nối phụ thuộc, PR gates, ranh giới chung. Skill là hướng dẫn + tài nguyên, không tự cấp tool grant và không phải workflow runtime riêng.

**Phụ thuộc:** `SPEC-AGENT-001`, `SPEC-RUNTIME-001`, `SPEC-SECURITY-001`; authoritative detailed scope `references/CR-SKILL-001.md`

**Không nằm trong scope:** Marketplace federation, unsigned third-party code execution, custom Python engine, agent-generated Skill with automatic publication, skill-based expansion of tool permissions.

## 2. Mapping sang CSDL 56 bảng

| New target table (within 56) | Purpose |
|---|---|
| `catalog.skills` | mutable identity/draft, workspace-scoped status |
| `catalog.skill_versions` | immutable version + artifact digest and manifest |
| `catalog.agent_skill_bindings` | pinned `(agent_version_id, skill_version_id)` |

No `skill_permissions`, `skill_runs` or `skill_marketplace` in MVP. Immutable bundles live in object storage; credentials never in artifacts. Other owners: `catalog.agent_versions` published configuration, `governance.audit_events`, `runtime.run_events` metadata showing loaded skills. DB migration is +3 table additive; apply and RLS verify via `DB-56_MIGRATION_IMPLEMENTATION.md`.

## 3. Luồng hoạt động và domain invariants

1. Import source bundle in secure staging; reject zip slip, symlink traversal, oversized archive, nested archive abuse and unsupported executable payload paths. Parse `SKILL.md` frontmatter required fields exactly as Agent Skills standard; check naming collisions and validity.
2. Separate metadata discovery from content load. Agent first sees authorized `name`, `description`, version/reference; only load full instructions when selected; load references/assets individually and within context budget.
3. Audit security: description/body/resources are lower-trust instructions relative to platform policy. Model may choose skill but loader enforces AgentVersion binding + published status + tenant/workspace.
4. Publish immutable `catalog.skill_versions` and bundle SHA with canonical ordering; then bind pinned version when AgentVersion published, transactionally. No floating `latest` on run.
5. Runtime records skill selection/load outcome/bytes/token cost in trace, linked `run_id`, without raw sensitive artifacts in audit.
6. Skill may refer to Tool/remote MCP Tools but `SPEC-TOOL-001` verifies current grants and approval; Skill cannot invent permission, alter workflow or bypass approval.
7. **MVP script execution disabled**. If later enabled: isolated sandbox, no host credentials, outbound network deny/allowlist, resource limits, explicit policy and separate tests. Merely having `scripts/` in package never enables execution.
8. Revocation/disabled status after publish must block future loads even if AgentVersion remains pinned; cached bundles honor revocation metadata.

## 4. API contracts & authorization

Follow exact endpoints and schemas in `references/CR-SKILL-001.md`. Required operations: import/create, validate, get/list, versions, publish, bind, preview/test; all workspace scoped with `skill:read|create|publish|bind` permissions. Never let Agent Runtime call management publish API. For any duplicate endpoints in CR vs existing router, preserve one canonical route and record mapping in OpenAPI. `agent_skill_bindings` must be managed through Agent publish flow rather than UI independent mutation of frozen version.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

Authoritative portable format `SKILL.md` with frontmatter `name` and `description` plus controlled optional resources. Canonical `SkillRef {skillVersionId, manifestHash, bundleHash}`; `SkillLoadResult {skillVersionId,loadedResources[],approxTokens,blockedReason?}`. Distinguish *skill discovered* from *skill read* from *tool invoked* in trace. Source trust marked lower than system/policy.

Full detailed validations, DB columns and API response semantics remain those in CR; if conflict, open ADR and update both documents, do not silently override.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
apps/control-api/src/control_api/modules/skills/{router.py,schemas.py,service.py,repository.py,validator.py,publisher.py}
apps/agent-runtime/src/agent_runtime/skills/{registry_client.py,discovery.py,loader.py,context_injector.py,execution_policy.py}
apps/studio-web/src/features/skill-management/{SkillEditor.tsx,SkillVersionList.tsx,SkillTestPanel.tsx,SkillSelector.tsx}
contracts/jsonschema/skill-manifest.v1.schema.json
skills/<example-skill>/SKILL.md
tests/{unit/skills,security/skills,e2e/agent_skill_load.py}
```

## 7. Security, privacy và kiểm soát quyền

Guard import/parser/archive size, paths and content. No direct host Bash from untrusted Skill. Scope object reads to pinned bundle and tenant. Validate script refs are only file names, never runtime host paths. Skill instructions or linked resources may include prompt injection: do not promote trust, never grant tool permission, prevent exfiltration through tools. Signature/provenance and malware scan if external third-party libraries supported.

## 8. Lỗi, retry, idempotency và recovery

Unpublished version cannot be loaded; revoked skill fails closed; cache invalidation required. Partial upload keeps staging untrusted, no published metadata. Import repeated with same idempotency key and payload produces same result, changed hash conflict. Error codes `SKILL_INVALID`, `SKILL_NOT_PUBLISHED`, `SKILL_NOT_BOUND`, `SKILL_REVOKED`, `SKILL_RESOURCE_DENIED`, `SKILL_CONTEXT_BUDGET`, `SKILL_ARCHIVE_REJECTED`.

## 9. Test matrix / acceptance evidence

| ID | Test | Acceptance |
|---|---|---|
| SKILL-01 | Valid SKILL.md | validate, publish immutable, binding exact version |
| SKILL-02 | Unauthorized Skill version | tenant/binding check prevents load |
| SKILL-03 | Two AgentVersions using same Skill | pinned versions deterministic |
| SKILL-04 | Skill malicious tool instruction | tool grant/approval unchanged |
| SKILL-05 | Attempt uploaded script execution | policy blocks, no subprocess |
| SKILL-06 | Zip traversal/symlink/archive bomb | rejected before object write |
| SKILL-07 | Revoked Skill after publish | runtime fails closed, audit/trace |
| SKILL-08 | Progressive disclosure | metadata-only initial context; measured on-demand reads |
| SKILL-09 | Imported same name cross-tenant | no leakage/collision across tenancy |

## 10. Kế hoạch Pull Requests

Execute **SK-PR01 → SK-PR07** exactly as described in `references/CR-SKILL-001.md`; prerequisites Agent Registry and Runtime interfaces. Progression contracts/validator → SQL/RLS → CRUD/version API → binding → runtime loading → UI → security/E2E. Avoid implementing executable scripts in these PRs unless an explicit later ADR changes scope.

## 11. Observability / metrics / operations

Skill discovery/load counts, per-run context tokens, invalid manifest types, revocation-deny counts, retrieval resource latency, bundle checksums, test-case routing quality. Audit metadata, redact skill body/assets if proprietary.


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

- https://agentskills.io/specification
- https://docs.agno.com/skills/overview
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
