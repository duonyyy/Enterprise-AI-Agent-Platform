# SPEC-SECURITY-001 — Identity, Tenant/Workspace Isolation, Policy & Governance

**Status:** SECURITY SPEC / UNVERIFIED · **DB:** `DB-BASELINE-56` · **Priority:** P0 · **Owner:** Control API + Platform Security

## 1. Trust model and boundaries

**Humans and services are principals; LLM outputs, Skills, MCP server metadata, tool results and uploaded documents are untrusted data.** Use verified JWT/OIDC subject or authenticated service-account identity; never infer tenant/workspace or role from prompt, header chosen by client without validation, raw AgentSpec or model output.

Separation:
- **Authentication**: verify issuer, signature, audience, expiry and token semantics; reject revoked/disabled service accounts.
- **Authorization**: decide operation-specific `allow/deny` on `(principal,workspace,resource,action,context)`.
- **Enforcement**: API and Tool Gateway enforce decision; Workflow/LLM cannot bypass.
- **Data isolation**: PostgreSQL RLS + scoped DB grants + workspace membership + object/vector access filters.
- **Audit**: append redacted provenance and decision to `governance.audit_events`, not chain-of-thought or secrets.

Reference policies from NIST AI RMF and OWASP Agentic AI risks; prompt guardrails alone **never** replace authorization controls.

## 2. Database mapping — use 56, do not duplicate IAM

| Table | Purpose |
|---|---|
| `iam.tenants`, `iam.users`, `iam.workspaces` | Tenant lifecycle, global identities, workspaces |
| `iam.memberships`, `iam.service_accounts` | Role scoped to workspace; machine identity |
| `governance.policy_sets`, `governance.policy_versions` | Immutable policy revisions (if published), evaluation source |
| `governance.audit_events` | Recorded decision, actor, resource, outcome |
| `governance.retention_policies` | Data retention per workspace/category |
| `catalog.tool_grants` | Narrow tool access on role/service account |
| `catalog.agent_publications` | Environment separation and active version pointer |
| `catalog.skills`, `skill_versions`, `agent_skill_bindings` | Skill permission/discovery; no skill-as-permission |
| `integration.connections`, `mcp_tool_mappings` | Server credentials reference and approved remote tool mapping |
| `runtime.approval_requests`, `approval_decisions` | Authorized human approval evidence |

All tenant-scoped tables carry `tenant_id`, and workspace-scoped tables additionally carry `workspace_id` and composite FKs; some global IAM tables require separate auth/access policy instead of a non-existent tenant_id column.

## 3. Permission matrix — proposed policy contract

| Action | owner | admin | developer | operator | viewer |
|---|---|---|---|---|---|
| `agent:read` | Y | Y | Y | Y | Y |
| `agent:create/edit` | Y | Y | Y | N | N |
| `agent:publish` | Y | Y | policy | N | N |
| `tool:create/publish` | Y | Y | policy | N | N |
| `tool:grant` | Y | Y | N | N | N |
| `mcp:connection:manage` | Y | Y | policy | N | N |
| `mcp:tool:approve` | Y | Y | N | N | N |
| `skill:publish` | Y | Y | policy | N | N |
| `run:start/read` | Y | Y | Y | Y | read only |
| `approval:decide` | policy | policy | policy | policy | N |
| `audit:read` | Y | Y | N | policy | N |

`policy` means determined by verified policy version / approver role, not allowed by default. This is a **design proposal**, not a claim that these roles are already in code. Approver may not approve their own high-impact request where separation-of-duties policy requires independence. Server-side policy is authoritative; UI only displays permissions, not enforces them.

## 4. Tenant + workspace enforcement

- Validated JWT principal resolves to `(user_id, memberships[tenant,workspace,role])`; API must resolve workspace from trusted path and membership.
- DB uses separate migration owner and restricted runtime roles; no runtime `SUPERUSER`, `BYPASSRLS`, or owner privileges to bypass RLS.
- For tenant data, open DB transaction; set `app.tenant_id` server-side with `SET LOCAL` / `set_config(..., true)` from the verified principal; then run scoped statements in same transaction. Reset automatically on commit/rollback. This is defense-in-depth, not protection from arbitrary SQL execution.
- RLS `USING` filters reads/deletes/updates; `WITH CHECK` blocks cross-tenant inserts/updates. Test negatives with actual app role.
- Workspace membership must be checked separately (RLS by tenant alone permits visibility across workspaces **within same tenant** to the same DB role unless further scoped in queries/policies).
- Object storage: namespace keys by tenant/workspace and pin immutable version hash; signed URLs short TTL and authorized per object.
- Qdrant: attach tenant/workspace/document visibility filters on every query; never let the model control filters without validation.
- Audit attempts even when denied; avoid leaking existence via different response timing/body where material.

## 5. Security considerations for Skills & MCP

- Skill bundle input path validation rejects absolute paths, traversal, symlink/hardlink, escaping links, device files, oversized archives and suspicious resource counts; content is untrusted instructions. `allowed-tools` does not grant access.
- Skill scripts are **non-executable** in MVP. Future sandbox requires separate risk review/ADR (resource caps, isolation, egress, syscalls and credential access).
- MCP remote URL policy: prefer HTTPS Streamable HTTP; endpoint allowlist, no secrets in URLs, check resolved IP and redirects, DNS rebinding protection. Prefer stateless protocol 2026-07-28 and SDK adapter for legacy clients.
- `tools/list` and `resources/list` are discovery only; new capabilities require review, never automatic activation.
- Tool outputs and knowledge documents can contain prompt injection. Treat as data not policy or system instructions.
- Secrets in vault/secret manager; DB stores only `secret_ref`; sanitize exceptions, logs, traces, debug captures and metrics attributes.

### 5.1 Tầng Phòng thủ Nâng cao (VinAI Capstone & AgentArena Standards)
- **Zero-Width Unicode Payload Sanitization (`ZeroWidthShield`):** Quét sạch toàn bộ các ký tự vô hình (Zero-Width Space `\u200B`, Zero-Width Non-Joiner `\u200C`, Zero-Width Joiner `\u200D`, Bidi Overrides) trong văn bản đầu vào để vô hiệu hóa tấn công Indirect Prompt Injection tàng hình.
- **Vietnamese PII Anonymization (`PiiScrubber`):** Tự động phát hiện và che giấu dữ liệu định danh cá nhân đặc thù Việt Nam (Số CCCD/CMND 12 chữ số, SĐT 10 chữ số, Email, Số thẻ thanh toán) thành `[REDACTED_*]` trước khi gửi tới Model Gateway hoặc ghi log/trace.
- **AST Security Scanner (`AstSandbox`):** Phân tích cú pháp tĩnh cây cú pháp trừu tượng, chặn đứng mọi lời gọi hàm nhạy cảm nguy hiểm (`os.system`, `subprocess`, `eval`, `exec`, `open` ghi file hệ thống).
- **Cô lập Bộ ba Nguy hiểm (Lethal Trifecta Isolation):** Nghiêm cấm cấu hình Agent đồng thời sở hữu cả 3 điều kiện: (1) Nhận văn bản đầu vào chưa kiểm chứng, (2) Truy cập kho tri thức nhạy cảm, và (3) Gọi công cụ ghi dữ liệu ra hệ thống ngoài, trừ khi có phê duyệt tường minh qua Cổng Phê duyệt Con người (HITL Gate) với mã hash HMAC bất biến.

## 6. Policy engine integration design

Proposed evaluation interface:

```json
{
  "principal": {"kind":"user","subject":"verified-subject","workspaceId":"workspace-id","roles":["developer"]},
  "action":"tool:invoke",
  "resource": {"kind":"tool_version","id":"pinned-tool-version-id","risk":"write"},
  "context": {"agentVersionId":"pinned-agent-version","runId":"run-id","approvalId":null}
}
```

Return `{allow:false, reasonCode:"TOOL_APPROVAL_REQUIRED", obligations:["human_approval"]}` or explicit allow. Implement policy in existing service first; OPA adapter optional after actual need. If policy evaluation fails, default deny. Policy changes emit versioned audit/outbox events. Avoid relying on natural-language LLM classification for permission decisions.

## 7. Authorization at API and Gateway

Control API validates user identity and workspace membership on draft CRUD, publish, Skill/MCP administration, approvals and audit. Runtime authenticates trusted service-to-service requests, constrains allowed agent/workflow versions, and revalidates tool/skill bindings against current revocation state. Tool Gateway independently re-evaluates grants/policies right before each side effect. Cross-tenant and cross-workspace IDs are never trusted merely because they are syntactically valid.

## 8. Governance & observability

`governance.audit_events` includes actor subject, action, resource type/id, outcome, request id, trace id and `details_redacted`. Avoid raw prompts, bearer tokens, PII or entire tool outputs in audit. Use OpenTelemetry for spans; identify run and correlation ID; separate access to evaluation datasets and sensitive traces. Retention and right-to-delete workflows require policy and evidence, not unconditional hard deletes of audit history.

## 9. Security test matrix

| Test | Procedure | Pass |
|---|---|---|
| SEC-001 | No JWT / bad signature / wrong audience | 401 |
| SEC-002 | Valid JWT, wrong workspace | 403/404, no rows |
| SEC-003 | A tenant tries to bind B's SkillVersion | denied by composite FK + authz |
| SEC-004 | DB app role with tenant A context selects B | zero rows (or permission denial) |
| SEC-005 | DB role without tenant context | zero rows/deny for tenant-scoped tables |
| SEC-006 | Normal user tries to publish unauthorized Agent | 403 and audit denial |
| SEC-007 | Reuse approval decision across action hash | denied |
| SEC-008 | MCP catalog announces a new tool | not executable before review |
| SEC-009 | Malicious remote URL to metadata service | blocked before outbound request |
| SEC-010 | Skill `allowed-tools` requests admin tool | no grant; invocation denied |
| SEC-011 | Secrets in tool response / exception | redacted before audit, SSE, traces |
| SEC-012 | Concurrent policy revoke and tool execution | documented recheck point + deterministic outcome |
| SEC-013 | App runtime DB principal accidentally has BYPASSRLS | release gate fails |
| SEC-014 | Attempt to modify published SkillVersion or AgentVersion | blocked |

Tests must run with **non-bypass DB role** and two tenants + two workspaces in one tenant, plus revoked service account scenario.

## 10. Security PR plan & release gates

`SEC-PR01`: authenticated principal/roles/permissions contract; `SEC-PR02`: app DB roles/GRANT/RLS negative tests; `SEC-PR03`: API authorization on Registry, Skill, MCP and Approval; `SEC-PR04`: Gateway policy/revocation and audit; `SEC-PR05`: red team prompts, SSRF, token leakage, concurrency, CI fail gates.

**Exit:** Deny-by-default policy enforced at API and Tool Gateway, cross-tenant/workspace tests pass with application role, no secrets in artifacts; DB baseline stays 56. Production certification needs independent security and operational testing beyond these docs.

## References
- PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- Agno authorization modes: https://docs.agno.com/agent-os/security/overview
- OPA docs: https://www.openpolicyagent.org/docs/latest/
- MCP 2026: https://blog.modelcontextprotocol.io/posts/2026-07-28/
