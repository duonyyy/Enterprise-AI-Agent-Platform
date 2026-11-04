# AGENTS.md — Enterprise AI Agent Platform

> Repository-wide instructions for Codex and other coding agents. Public coding baseline: [`AGENT_CODING_RULES.md`](./AGENT_CODING_RULES.md). Collaboration: [`CONTRIBUTING.md`](./CONTRIBUTING.md). Architecture: [`ARCHITECTURE.md`](./ARCHITECTURE.md). Detailed specifications, approved policy, ADRs, audits and implementation runbooks are supplied separately through the maintainer's private documentation bundle.

## 1. Before editing code

- Identify the current branch, commit, git status, existing modules, migration head, and already implemented features. Do not assert code exists based on documentation.
- Read `CONTRIBUTING.md`; declare branch scope and owned paths before editing. Preserve existing working-tree and staged changes; do not switch/reset a dirty checkout to create a branch.
- Obtain the approved task packet from the maintainer: matching SPEC, accepted ADRs, policy/source-of-truth excerpts, and CR-SKILL-001 or CR-MCP-001 when applicable. Record document IDs/revisions and the matching code baseline. A local ignored documentation folder may provide the bundle; it is not part of a public clone. Do not assume every draft in that folder is approved.
- If a required specification, security policy or architectural decision is unavailable, mark the affected implementation BLOCKED and request the missing task packet. Continue independent read-only investigation or explicitly scoped work; do not infer authority from README examples.
- Treat the current SQL as an authored target of **56 domain tables / 7 PostgreSQL schemas**, not a claim that migrations have run. Schema history and approved deltas must be verified from the task packet and actual migrations.
- If canonical SQL/JSON Schema/OpenAPI contradicts a task description, stop and report the conflict; do not invent a schema, migration or endpoint.

## 2. Mandatory implementation boundaries

1. **Schema first:** Published specs, runtime payloads, APIs and events must conform to versioned `contracts/jsonschema/` and `contracts/openapi/` contracts.
2. **Server-side trust:** Authenticated principal and tenant/workspace scope come from trusted server-side state, not from model output, Skill, MCP, UI props or retrieved documents. Apply RBAC/policy plus RLS.
3. **Single Tool Gateway:** Every Tool operation (`http`, `mcp`, `builtin`) follows the **12-Step ToolDispatcher** in the full rules; no direct side-effect calls from a graph, Skill, MCP adapter or frontend.
4. **Real idempotency:** `runtime.tool_operations` uses `UNIQUE (tenant_id,workspace_id,operation_key)`, with `request_hash`, `reserved/executing/succeeded/failed/unknown`. Never blind-retry ambiguous external writes and never promise exactly-once without upstream cooperation.
5. **Approvals:** High-impact operations require a trusted approved, unexpired `action_hash` bound to the exact canonical action; the model cannot self-approve.
6. **No unchecked execution:** No arbitrary `eval`, `exec`, shell or imported Skill scripts. MVP script execution/local MCP subprocess are disabled unless approved sandbox ADR is implemented.
7. **Secrets:** Use scoped `secret_ref`; never commit, log, trace, embed in specs or expose raw credentials. Enforce MCP OAuth audience and outbound SSRF/egress rules.
8. **Immutable publishing:** Published Agent/Tool/Workflow/Skill versions and content hashes cannot mutate; runs pin versions, and environment rollback changes only the publication pointer.
9. **Durable runtime:** LangGraph checkpoints + stable thread IDs; replay-safe tasks; bounded retries, budgets and cancellation; reconcile external effects after crash.
10. **No speculative expansion:** No new domain table beyond DB-56, enum/status, privileged service, microservice or duplicate Tool executor without approved ADR/migration.
11. **Evidence:** Every PR reports actual tests as `PASS` / `FAIL` / `NOT_RUN` / `BLOCKED` with commands and output. A document or mock is not production evidence.
12. **Human authority:** Do not deploy production, perform destructive migration, rotate secrets, grant access, approve business actions or execute unreviewed code without explicit authorized approval.

## 3. Required workflow per PR

- **Audit:** current behavior and affected trust boundaries.
- **Plan:** exact files/contract/migration impact, rollback and tests; smallest safe scope.
- **Implement:** preserve existing architecture, no unrelated refactors.
- **Verify:** schema validation, unit/integration tests, RLS/authorization, Tool Gateway denial, idempotency/restart as applicable.
- **Report:** changed files, test evidence, security risks, release gate, next smallest PR.

## 4. Hard stop conditions

Stop and label `BLOCKED` if a security-critical contract is unknown; migrations disagree; RLS cannot be verified for an applicable change; a high-impact external write lacks safe reconciliation; a Tool or Skill needs unauthorized execution; a secret would be exposed; or a required test cannot be run but would be claimed successful.

Read the full approved policy supplied privately before implementation that changes permissions, schemas, Agent publishing, runtime, Tool Gateway, Skills, MCP, approvals, knowledge ACL, secrets or deployment. The public coding guide is a minimum baseline, not permission to bypass a missing requirement. Do not commit the private bundle or paste its sensitive contents into public issues, PRs or logs; public handoffs use document IDs/revisions and a non-sensitive scope summary.
