# Agent Collaboration & Branching Guide

> **Status:** Required working agreement for human engineers and coding agents.  
> **Scope:** Branching, ownership, pull requests, and evidence for this repository.  
> **Does not override:** [`AGENTS.md`](../../AGENTS.md), [`AGENT_CODING_RULES.md`](../../AGENT_CODING_RULES.md), contracts, accepted ADRs, or database migrations.

## 1. Purpose

This repository is a schema-first, multi-tenant platform. A branch is not a private place to invent contracts, bypass security, or combine unrelated work. Each branch must have one clear outcome, a bounded file scope, and evidence that the changed behavior works or is explicitly blocked.

The default integration model is **short-lived branches into protected `main`**. Do not create a permanent `develop` branch: it would become an unverified second source of truth.

## 2. Branch model

| Branch | Who uses it | Rule |
|---|---|---|
| `main` | Everyone reads; authorized maintainers merge | Protected baseline. No direct push, force-push, or unreviewed change. |
| `chore/bootstrap-foundation` | Maintainer only, before first GitHub baseline | One reviewed bootstrap PR that establishes the initial repository baseline. Delete after merge. |
| `feat/<module>-<scope>` | Engineer or agent implementing one capability | New behavior within one module or a small vertical slice. |
| `fix/<module>-<issue>` | Engineer or agent fixing a verified defect | Contains the regression test when practical. |
| `test/<scope>` | Test owner | Tests or test infrastructure only; no production behavior change. |
| `docs/<scope>` | Documentation owner | Documentation or governance only. |
| `chore/<scope>` | Maintainer | Build, CI, dependency, repository, or tooling work. |
| `codex/<scope>` | Coding agent | Same review and CI requirements as every other branch. |

Examples:

```text
feat/control-api-rbac
feat/runtime-durable-checkpoints
feat/knowledge-ingestion-quarantine
fix/worker-outbox-retry
test/rls-cross-tenant-isolation
docs/agent-onboarding
chore/ci-lockfile-gate
codex/tool-gateway-idempotency
```

### Create a branch

Start from the latest approved `main`; never branch from another agent's unfinished feature without the maintainer's explicit instruction.

```powershell
git switch main
git pull --ff-only
git switch -c feat/<module>-<scope>
```

If the repository has not yet received its first GitHub baseline, work only on `chore/bootstrap-foundation` until that PR is reviewed and merged.

## 3. Ownership and overlap control

One agent may work on a small cross-cutting vertical slice, but must declare every owned path in its PR before editing. Do not run parallel work that edits the same high-conflict boundary.

| Owner group | Primary paths | May not change without coordination |
|---|---|---|
| Control Plane | `apps/control-api/` | contracts, migrations, RLS policies |
| Runtime | `apps/agent-runtime/` | Tool Gateway semantics, contracts, migrations |
| Worker/platform libraries | `apps/jobs-worker/`, `libs/python/` | event contracts, migrations |
| Studio | `apps/studio-web/`, `packages/` | OpenAPI/API payloads |
| Test/evaluation | `tests/`, `evals/` | production implementation unless task explicitly requires it |
| Contract and database owner | `contracts/`, `database/migrations/` | owns merge order for schema, OpenAPI, JSON Schema, RLS |

`contracts/` and `database/migrations/` are serialized boundaries. Only one approved PR may change a given migration chain or published contract at a time. A feature that needs either boundary must coordinate with the assigned owner before code is written.

## 4. Mandatory agent workflow

1. **Read:** `AGENTS.md`, this guide, the relevant `SPEC-*.md`, accepted ADRs, and contract/migration files.
2. **Audit:** Record branch, commit, `git status`, existing implementation, and affected trust boundaries. Documentation is not proof that code exists.
3. **Plan:** State the files, contracts, migrations, security/RLS impact, rollback, and tests. Stop when a canonical source conflicts with the task.
4. **Implement:** Keep scope small. Do not mix refactors, dependency upgrades, and product behavior in the same PR.
5. **Verify:** Run the smallest relevant real checks. Never report mock, static, or unexecuted work as runtime proof.
6. **Report:** Open one PR with evidence, limitations, and the next smallest task.

## 5. Non-negotiable boundaries

- Never accept tenant/workspace identity, permissions, approval, or tool authority from an LLM, Skill, MCP metadata, or browser payload.
- Every side effect goes through the Tool Gateway. Do not add RAM-only success paths, direct external writes, arbitrary shell execution, or fake IDs/results.
- Do not alter an applied migration. Add a forward migration; database and contract changes must be versioned and reviewed together.
- Never commit `.env`, secrets, local databases, `node_modules`, `.venv`, `.next`, or test/build caches.
- Do not auto-resolve a conflict by deleting another agent's work. Stop and ask the maintainer to decide the integration order.
- Do not deploy, rotate secrets, grant access, or execute high-impact business actions without explicit authorized human approval.

## 6. Pull-request contract

Each PR must contain:

```markdown
## Scope
- Branch: `<name>`
- Owned paths: `<paths>`
- Contract/migration impact: `none` | `<files and versioning plan>`

## Security and data boundaries
- Tenant/workspace enforcement:
- Tool Gateway / approval / idempotency impact:
- Secrets and external side effects:

## Evidence
- PASS: `<command and concise result>`
- FAIL: `<command and failure>`
- NOT_RUN or BLOCKED: `<reason and required environment>`

## Rollback and follow-up
- Rollback:
- Next smallest PR:
```

The PR author must say `NOT_RUN` or `BLOCKED` when Docker, PostgreSQL, a provider, or a required credential is unavailable. A green unit test does not prove RLS, external side effects, production deployment, or model quality.

## 7. Minimum local checks

Run only the checks relevant to the change, then report their real result. A safe baseline is:

```powershell
uv lock --check
uv run python -B -m compileall -q apps libs tests
uv run pytest tests/contract/ apps/control-api/tests apps/jobs-worker/tests -v
uv run python docs/03-database/database/validate_db_static.py
pnpm --filter studio-web build
```

For migration, RLS, or Compose changes, also run the Docker-backed integration checks once Docker Desktop is available. Do not claim them as passed otherwise.

## 8. Merge requirements for `main`

Configure the GitHub branch rule for `main` to require:

1. Pull request merge only; no direct pushes or force pushes.
2. At least one human approval.
3. Passing required CI jobs: Python compilation/schema validation, contract/integration tests, Studio build, and Compose build gate.
4. Resolved review conversations.
5. Up-to-date branch before merge when a contract or migration changed.

Delete the feature branch after merge. `main` remains the only long-lived engineering baseline.

After the new GitHub repository and teams are created, copy `.github/CODEOWNERS.example` to `.github/CODEOWNERS` and replace every placeholder with a real user or visible team that has write access. Only then enable GitHub's required code-owner review rule. A placeholder owner is not protection.

## 9. Definition of done for an agent task

An agent task is done only when its PR is narrowly scoped, contracts are respected, required tests have truthful evidence, and no unresolved security or migration conflict remains. If any of those conditions are unknown, the correct outcome is `BLOCKED`, not a speculative implementation.
