# COMPLETE MODULE SPEC INDEX — Enterprise AI Agent Platform / DB 56

**Date:** 2026-10-30 · **Status:** SPEC PACKAGE ONLY / NOT AN IMPLEMENTED REPOSITORY · **Canonical DB:** 56 target domain tables / 7 PostgreSQL schemas.

## Full inventory

| Specification | Responsibility | Priority | Source |
|---|---|---|---|
| `SPEC-SECURITY-001` | Identity, RBAC, tenant RLS, policy, audit | P0 | Existing P0 spec |
| `SPEC-AGENT-001` | Agent Registry, declarative AgentSpec, immutable versions/publish | P0 | Existing P0 spec |
| `SPEC-RUNTIME-001` | LangGraph execution harness, runs, checkpoints, recovery | P0 | Existing P0 spec |
| `SPEC-TOOL-001` | Shared Tool Gateway, grants, idempotency | P0 | Existing P0 spec |
| `SPEC-WORKFLOW-001` | Workflow DSL, human approvals, resume, digital workers | P0 | Existing P0 spec |
| `SPEC-LLM-001` | Model Gateway, provider capabilities, budgets and usage | P1 / basic adapter early | New |
| `SPEC-SKILL-001` | Skills registry, discovery, progressive loading, binding | P1 | New, defers detail to CR-SKILL-001 |
| `SPEC-MCP-001` | Remote MCP connections, discovery, review, invocation | P1 | New, defers detail to CR-MCP-001 |
| `SPEC-KNOWLEDGE-001` | Document ingestion, Knowledge RAG, Qdrant access, citations | P1 | New |
| `SPEC-MEMORY-001` | Conversation, checkpointer, long-term memory and context budgets | P1 | New |
| `SPEC-EVAL-001` | Datasets, grading, traces, release gates | P1 | New |
| `SPEC-STUDIO-001` | Next.js Agent Studio, visual workflow/run/approval UI | P1 | New |
| `SPEC-DEPLOY-001` | CI/CD, IaC, migrations, backups, DR, production hardening | P2; CI P0 | New |

**Total: 13 SPEC** (5 reused unchanged + 8 new). Detailed `CR-SKILL-001.md` and `CR-MCP-001.md` remain normative for their respective extension requirements; these are referenced, not discarded.

## Canonical architecture and conflict resolution

- System of record for domain schema: `docs/00-governance/CANONICAL_DATABASE_56.md` and `references/ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql` (50 existing DDL) plus reviewed Skill/MCP additive migrations (+6). No production claim until database proof.
- Control API is modular monolith; LangGraph is chosen execution engine for initial Runtime; UI is Next.js; Postgres authoritative; Qdrant vector index; object storage for artifacts; Redis optional. Do not tie this independent platform to unrelated repositories.
- `SPEC-TOOL-001` has sole authority over Tool Gateway; MCP only registers/discovers/normalizes. `SPEC-SECURITY-001` governs authorization. `SPEC-WORKFLOW-001` owns human approval. `SPEC-AGENT-001` publishes pinned versions; `SPEC-RUNTIME-001` owns runs/checkpoints. `SPEC-MEMORY-001` cannot replace checkpointing. `SPEC-DEPLOY-001` controls release proof.
- In conflict between proposed API/path examples and running repo, audit source, write mapping + ADR before duplicating an endpoint/module. Existing P0 specs reused without silent rewriting.
- **No new 57th domain table** without explicit ADR + migration review. Infra/framework table counts must be reported separately.

## Read-first sequence for coding agent

1. `README.md`, `docs/00-governance/CANONICAL_DATABASE_56.md`, `docs/00-governance/DB-56_MIGRATION_IMPLEMENTATION.md`.
2. `docs/00-governance/SOURCE_OF_TRUTH.md` and `IMPLEMENTATION_ORDER_FULL_56.md`.
3. Original baseline SQL + data dictionary + existing runbook from `references/`.
4. Module SPEC for the PR to implement + cross-module dependencies listed therein.
5. For Skill/MCP, read full `references/CR-SKILL-001.md` and `references/CR-MCP-001.md`.
6. Audit real local source read-only before editing; tests prove implementation.

## Scope limits

This archive does not change local source or apply migrations. All APIs, file paths, and PR IDs in new SPECs are proposed contracts until implemented and validated against the actual repository. Dedicated Qdrant indexes, tests and migration DDL must still be authored and executed.
