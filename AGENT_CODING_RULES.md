# AGENT_CODING_RULES.md — Condensed Rules for AI Coding Agents

> **Audience:** Codex, Cursor, Claude Code, Antigravity, Copilot, and human reviewers.  
> **Source of Truth:** Full specification at [`docs/00-governance/CODING_STANDARDS_AND_ARCHITECTURE_RULES.md`](./docs/00-governance/CODING_STANDARDS_AND_ARCHITECTURE_RULES.md).  
> **Status:** APPROVED POLICY (v1.0 Baseline) | **Target DB:** 56 tables / 7 schemas.

---

## 1. Absolute Invariants (Hard Stops — Non-Negotiable)

1. **Schema-First:** Published configs, payloads, and APIs MUST conform to `contracts/jsonschema/` and `contracts/openapi/`. Do not invent fields, enums, or ad-hoc JSON.
2. **Server-Side Trust:** Authentication and tenant/workspace scope come strictly from server-side state (`IdentityPrincipal`). LLM prompts, Skills, MCP descriptions, and UI props are **untrusted data**.
3. **Single Tool Gateway:** Every tool (`builtin`, `http`, `mcp`) MUST execute through the **12-Step ToolDispatcher** (`SPEC-TOOL-001`). No direct side-effects from graphs, skill scripts, or UI.
4. **Target DB-56 Only:** Strictly 56 domain tables across 7 schemas (`iam:5`, `catalog:16`, `knowledge:7`, `runtime:12`, `integration:6`, `governance:4`, `ops:6`). Never invent a 57th table without an approved ADR.
5. **Real Idempotency:** Mutations use `UNIQUE (tenant_id, workspace_id, operation_key)`. Timeouts during external writes are recorded as `unknown` — reconcile before retrying; never blind-retry.
6. **Zero Plaintext Secrets:** Sensitive credentials use scoped `secret_ref`. Never hardcode, log, trace, or expose secrets to browser bundles.
7. **Immutable Snapshots:** Published versions (`agent_versions`, `skill_versions`, `tool_versions`, `workflow_versions`) are frozen with `bundle_sha256`. Rollback changes only the publication pointer (`agent_publications`).
8. **Action-Bound Approvals:** High-risk actions require CAS approval bound to exact canonical `action_hash`. The model cannot self-approve.
9. **Evidence Required:** Report test results honestly as `PASS`, `FAIL`, `NOT_RUN`, or `BLOCKED` with actual CLI commands. Never claim PASS without executing.

---

## 2. Python 3.12+ Implementation Rules

- **Typing (`mypy --strict`):** 100% type annotations on all public functions and methods. Forbid `Any` and unvalidated `dict[str, Any]`. Use `T | None` explicitly.
- **Data Models:** Use `@dataclass(frozen=True, slots=True)` for domain value objects. Use Pydantic v2 with `extra="forbid"` for untrusted API/boundary DTOs.
- **Ports & Adapters:** Define dependency contracts with `typing.Protocol`. Domain logic MUST NOT import `fastapi`, `sqlalchemy`, `langgraph`, `httpx`, or cloud SDKs.
- **Exceptions:** Catch specific exceptions only. Never use `except Exception: pass` or bare `except:`. Always preserve context: `raise DomainError(...) from exc`.
- **Async Concurrency:**
  - One SQLAlchemy `AsyncSession` per async task scope — never share a session across `asyncio.gather`.
  - Cap concurrency with `asyncio.Semaphore`. Never spawn unbounded tasks on user input.
  - Keep DB transactions short; release row locks before remote I/O (LLM, MCP, HITL wait).
- **Precision:** Use `Decimal` for currency and token costs; use timezone-aware UTC (`datetime.now(timezone.utc)`). Never use `float` for money.
- **Logging:** Structured logging (`structlog`) with `trace_id`, `request_id`, `tenant_id`. Redact secrets and PII automatically. Never use `print()`.

---

## 3. TypeScript & Next.js 15 Implementation Rules

- **Strictness:** `strict: true`, `noUncheckedIndexedAccess: true`. Never use `any` or `as any`.
- **Discriminated Unions:** Model UI states and run lifecycle with tagged unions (e.g., `{ status: "idle" | "saving" | "error" }`). Handle all cases exhaustively with `assertNever(x: never)`.
- **Server Components First:** Default to React Server Components. Add `"use client"` only for interactive leaves (buttons, forms).
- **Client Security:** Never import server secrets into client files (`import "server-only"`). Prevent double-submit on buttons with disabled pending state.
- **Safe Spread:** Never use `...userInput` directly on privileged API mutation payloads. Allowlist fields explicitly.
- **Semantic Nulls:** Prefer `??` (nullish coalescing) over `||`. Use `value ?? default` so that `0`, `""`, and `false` are preserved.

---

## 4. SQL & Persistence Rules

- **Parameter Binding 100%:** Never concatenate strings or use f-strings for SQL queries. Table/column names must come from static code allowlists.
- **Tenant Scoping:** Every SQL query must filter by `tenant_id` and `workspace_id`. PostgreSQL Force RLS is the second line of defense.
- **Avoid N+1:** Explicitly eager load relations (`selectinload`, `joinedload`) on list queries.
- **Bounded Pagination:** Always set a max limit; sort with a deterministic tiebreaker (`ORDER BY created_at DESC, id DESC`).

---

## 5. AI Runtime & Tool Execution Rules

- **Untrusted LLM Output:** Parse model output into typed schemas before routing or tool invocation. Never pass model text to `eval()`, shell, or raw SQL.
- **Fixed Budgets:** Every agent loop must have bounded `max_steps`, hard timeouts, token budgets, and cancellation checks.
- **Pinned Dependencies:** Execution runs pin immutable version IDs; never resolve to dynamic `"latest"` during an active run.
- **SSRF Defense:** MCP and HTTP tool calls must pass 4-layer SSRF filter (block loopback, RFC 1918 private IPs, link-local, and cloud metadata `169.254.169.254`).

---

## 6. Testing & Quality Gates

- **AAA Structure:** Structure every test as Arrange — Act — Assert. Name tests by behavior: `test_publish_rejects_stale_revision`.
- **Parametrization:** Cover edge cases (empty, boundary, negative, max) using `@pytest.mark.parametrize`.
- **Mock at Seams:** Mock only external `Protocol` ports, never internal domain logic.
- **Security Negative Tests:** Test cross-tenant isolation by asserting that accessing another tenant's resource returns `403` or `404`.
- **Deterministic:** Freeze clocks (inject clock/time fixture) and random seeds. Never use `time.sleep()` in unit tests.

---

## 7. Merge-Blocking Antipatterns (Cheat Sheet)

| # | ❌ Antipattern (Merge Blocker) | ✅ Correct Production Pattern |
|---|---|---|
| 1 | `except Exception: pass` | Catch specific exception, log context, re-raise with `from exc` |
| 2 | `Any` or `dict[str, Any]` everywhere | Use Pydantic DTOs / frozen dataclasses / Protocols |
| 3 | `eval()`, `exec()`, or raw shell execution | Canned tool execution via 12-Step ToolDispatcher sandbox |
| 4 | Shared `AsyncSession` in `asyncio.gather` | One `AsyncSession` per task scope |
| 5 | Dynamic SQL string interpolation (`f"SELECT... {val}"`) | Parameter binding via SQLAlchemy `select()` |
| 6 | Returning `success: true` on external write timeout | Record `unknown` state and trigger reconciliation |
| 7 | Client-supplied `is_approved: true` | CAS server-side approval verification against `action_hash` |
| 8 | `"use client"` on entire page trees | Server Component wrapper with small Client Component leaves |
| 9 | `as any`, `!`, or `# type: ignore` without reason | Type narrowing, type guards, or documented narrow exceptions |
| 10 | Hardcoded timeouts/retries across modules | Centralized `BaseSettings` with explicit budgets |
| 11 | Tests asserting only `status_code == 200` | Assert domain state changes, outbox events, and receipts |
| 12 | Querying without tenant filter and limit | Always scope by `(tenant_id, workspace_id)` + bounded limit |

---

## 8. PR Evidence Template (Copy-Paste into PR)

```markdown
## SPEC / CR
SPEC-... or CR-... / implementation task

## Scope
- Changed files:
- Contract/DB impact (DB-56 check):

## Evidence
| Gate | Command | Result | Evidence |
|---|---|---|---|
| Format/Lint | `uv run ruff check .` | PASS/FAIL/NOT_RUN/BLOCKED | 0 errors |
| Typecheck | `uv run mypy ...` | PASS/FAIL/NOT_RUN/BLOCKED | 0 errors |
| Tests | `pytest tests/contract/` | PASS/FAIL/NOT_RUN/BLOCKED | 7 passed |
| Security/RLS | `python docs/03-database/database/validate_db_static.py` | PASS/FAIL/NOT_RUN/BLOCKED | 56 tables, 55 RLS |

## Rollback Plan
- Revert commit / pinned version pointer.
```

