# Full Implementation Order & PR Dependency Gates (56 domain tables)

**Status:** proposed schedule, not implementation proof. **Rule:** inspect git + live schema before work.

## Phase 0 — Bootstrap / verified foundations

- **GATE-00:** inspect current repository tree, commit, module implementations, migration versions and test runner; classify `IMPLEMENTED`, `PARTIAL`, `DOCUMENTED_ONLY`, `NOT_VERIFIED`.
- **DB-01/02/03:** restore baseline SQL (50) and add reviewed Skill (+3) and MCP (+3) migrations on disposable PostgreSQL; compare inventory 56 with 7 schemas. Exercise constraints, read/write RLS with non-bypass roles, migration repeatability, index inventory; snapshot logs.
- **SEC-PR01..:** establish authenticated principal, workspace isolation, transaction-local RLS, grants/audit; no wide privilege shortcuts.
- **LLM-PR01:** deterministic fake model provider adapter and contract (no vendor key needed yet).

## Phase 1 — Vertical Agent Core

- **AG-PR01..:** immutable AgentSpec validator, Agent Registry, publish/version pinning.
- **RT-PR01..:** execute published AgentVersion, durable run events/checkpoint/cancel/retry.
- **TL-PR01..:** common Tool Gateway + approved local read/mock tool + idempotency receipts.
- **LLM-PR02..03:** one provider integration (after secret manager and policy), token budgets.
- **GATE-CORE:** API `create → publish → run → mock tool → trace`, cross-tenant deny, repeat idempotent calls.

## Phase 2 — Capabilities and controlled side effects

- **SK-PR01..07:** contracts, import/validate, 3 SQL tables, bound versions, progressive loading, UI, evals; no arbitrary scripts.
- **MCP-PR01..08:** SDK/contract, 3 SQL tables, connections, discovery/schema review, approved tool mapping, Gateway adapter, UI, security tests.
- **WF-PR01..:** DSL/compiler, approval/HITL, restart/resume/reconciliation, Digital Worker.
- **GATE-CAPABILITIES:** pinned Skill + approved MCP mock tool + human approval → restart → resume → *one* controlled side effect and audit.

## Phase 3 — Data, memory, evaluation and user experience

- **KN-PR01..05:** upload, ingestion, Qdrant tenant filters, retrieval/citation, rebuild.
- **MEM-PR01..05:** conversations, checkpointer distinction, consented long-term memory, context budget.
- **EV-PR01..05:** dataset versions, graders, telemetry, release gates.
- **UI-PR01..06:** first thin UI slices may ship earlier; full Studio after APIs exist.
- **GATE-PRODUCT:** evidence from real Postgres + Qdrant + object storage, dashboard and golden eval regression.

## Phase 4 — Production hardening

- **OPS-PR01..06:** CI, staging migrations, IaC, separate roles and secrets, backups/DR, release and chaos gates. OPS-PR01 CI must start in Phase 0 rather than waiting until Phase 4.
- **GATE-PROD:** threat model, audit, non-bypass RLS, restore drill, approval retry/restart, cost quotas, regression gate, monitored rollback.

## Required PR report template

```markdown
### PR ID and status
- Source commit SHA / migration head:
- Scope and touched files:
- Existing functionality reused:
- Contract changes:
- DB changes (or none; table inventory before/after):
- Security/permissions/tenancy:
- Tests: command → result → evidence location:
- Unexpected behavior, blocked work, ADR required:
- Recovery/rollback strategy:
- Next PR allowed by gate:
```

**Do not** mark a phase complete based on markdown, mocks alone, or generated fixture data not run against real dependencies.
