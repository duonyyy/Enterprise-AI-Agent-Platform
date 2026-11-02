# SPEC-RUNTIME-001 — Agent Runtime, Harness, Runs & Durable Execution

**Status:** IMPLEMENTATION SPEC / NOT RUN · **DB:** `DB-BASELINE-56` · **Priority:** P0 · **Owner:** Agent Runtime

## 1. Scope and architecture

Run a **published, pinned AgentVersion**, not a mutable Agent draft. Provide LLM orchestration via LangGraph adapter, bounded execution, policy hooks, tool gateway integration, durable checkpoint and run event streaming. Handle cancellation, timeouts, retry and recovery; record trace & metrics without creating a second registry.

Execution Plane has no permission to change `catalog.agent_versions`, `catalog.skill_versions`, `integration.mcp_tool_mappings`, or policies. `Control API` authenticates user and controls settings; Runtime verifies trusted service principal and scoped authorization for every run.

Non-goals: autonomous code execution; in-process background jobs as durable queue; unlimited ReAct loop; installing Agno and LangGraph simultaneously without PoC; starting separate services for each concern; new DB tables beyond 56 without ADR.

## 2. Persistent state and data ownership

| Table | Runtime use | Caveat |
|---|---|---|
| `runtime.runs` | Run root, `status`, `agent_version_id`, request idempotency key | One run pinned to one published version |
| `runtime.run_steps` | Node attempts and status | Unique `(run,step_key,attempt)` |
| `runtime.run_events` | ordered redacted append-only timeline, SSE replay | atomic `next_event_seq` allocation under run lock |
| `runtime.checkpoint_refs` | pointer (`thread_id`,`checkpoint_id`,`namespace`) | Pointer != checkpoint data; configure actual durable LangGraph saver |
| `runtime.jobs` | durable queue `run/resume/reconcile` | claim by lease, not naive in-memory queue |
| `runtime.tool_operations` | dedup and `unknown` outcomes | `unknown` cannot be automatically replayed for writes |
| `runtime.conversations`,`messages` | user-visible conversation history | Distinct from checkpointer and long-term memory |
| `runtime.memory_records` | long-term facts/profiles with consent | Never conflate with run checkpoint |
| `runtime.approval_requests`,`approval_decisions` | waiting approval and decision | Control/API is authority for approval |
| `catalog.agent_versions`, `agent_skill_bindings`, `agent_tool_bindings`, `agent_knowledge_bindings` | read-only pinned capability manifest | Revalidate per-call revocation |
| `integration.mcp_tool_mappings` | approved MCP tool mapping | Existing gateway only |
| `governance.audit_events`,`ops.model_usage` | audit + measured usage | Do not fake model usage or grounding score |

`runtime.checkpoint_refs` cannot substitute LangGraph checkpoint storage. The LangGraph saver may create **framework-managed internal tables** depending on backend; count of **56 business-domain tables excludes migration/system/framework-private tables**. Such infrastructure tables must be explicitly inventoried, documented, and isolated in their own namespace if introduced. Never secretly add domain tables to make 56 false.

## 3. Modules and files

```text
apps/agent-runtime/src/agent_runtime/
  api/{runs_router.py,stream_router.py}
  application/{start_run.py,resume_run.py,cancel_run.py,get_run.py}
  compilation/{spec_validator.py,workflow_compiler.py,node_registry.py}
  engines/{base.py,langgraph_adapter.py}
  execution/{run_coordinator.py,step_executor.py,run_state.py,event_writer.py,job_worker.py}
  tools/{gateway.py,executor.py}
  context/{builder.py,budget.py,compaction.py}
  skills/{registry_client.py,loader.py}           # from CR-SKILL-001
  mcp/{tool_adapter.py,connection_resolver.py}    # from CR-MCP-001
  persistence/{run_repository.py,checkpoint.py,job_repository.py}
  safety/{input_guard.py,output_guard.py}
  observability/{tracing.py,usage.py}
  tests/{test_execution.py,test_recovery.py,test_cancel.py,test_stream.py}
```

Folder names are target structure; adapt to actual repo. Do not create duplicate `Tool Gateway` if existing executor meets contract.

## 4. Run state machine — match current DB CHECK

```text
queued -> running -> succeeded
    |        |   -> waiting_approval -> running -> succeeded
    |        |   -> failed / cancelled / timed_out
    |        -> failed / cancelled / timed_out
    -> cancelled
```

**Allowed statuses exactly match baseline**: `queued`, `running`, `waiting_approval`, `succeeded`, `failed`, `cancelled`, `timed_out`. Do not store `paused`, `interrupted`, `retrying`, `unknown` in `runtime.runs.status` without migration; represent retry attempts in `run_steps`/`jobs`. Terminal run cannot transition to active; cancellation races must be resolved with CAS, and late tool responses must not alter terminal results.

`runtime.run_steps.status`: `pending`,`running`,`waiting`,`succeeded`,`failed`,`skipped`. `runtime.jobs.status`: `queued`,`leased`,`done`,`dead`.

## 5. Run start contract and isolation

`POST /api/v1/agents/{agent_id}/runs` resolves publication in authorized environment; `POST /api/v1/agent-versions/{version_id}/runs` (optional privileged endpoint) pins explicit version. Require `Idempotency-Key` for externally triggered calls. `GET /runs/{run_id}`, `POST /runs/{run_id}/cancel`, `GET /runs/{run_id}/events?after=sequence`.

- Control API checks active membership and publication; sends internally signed service identity / user subject; Runtime re-verifies signature, audience, scopes and workspace.
- Validate input JSON schema and per-tenant budgets (steps, tool calls, tokens, total elapsed time, concurrency).
- Freeze `agent_version_id`, `workflow_version_id`, pinned dependencies and model selection at creation. `active` credential and grants still rechecked at execution.
- One transaction inserts `runtime.runs` + `runtime.jobs` + audit/outbox, idempotently on `(tenant,workspace,external_request_key)`.
- `external_request_key` nullable, so endpoint must require key when replay safety matters; duplicate key with different request digest needs separate verified mechanism because baseline does **not store a request digest**. This is an OPEN DESIGN DECISION: use canonical digest in immutable referenced event metadata and transactional comparison or approved additive migration **only with ADR**; no undocumented guarantee.

## 6. Worker claiming, leases and failure recovery

- Worker selects due `runtime.jobs` for its authorized tenant(s) by ordered `(available_at,id)` using `FOR UPDATE SKIP LOCKED` inside short transaction, sets lease and increments attempt. Do not hold DB transaction during LLM/tool network calls.
- Bounded `max_attempts`; reaper recovers expired leases; `dead` queue requires operator investigation.
- Heartbeat/lease extension with ownership check; no second worker may commit same step without comparing attempt token.
- Crash-after-external-side-effect is ambiguous: check `tool_operations` and upstream reconciliation API before retrying; mark `unknown` and require reconciliation, not blind retry.
- Write `run_events` and step transitions with transactionally allocated sequence; SSE delivery is at-least-once, client deduplicates `(run_id,sequence)`.
- `checkpoint_ref` persisted only after saver confirms durable checkpoint; rehydrate with identical `thread_id`, namespace and versioned graph. Deploy worker replicas only when checkpoint store/queue support it.

## 7. Harness lifecycle and gates

```text
pre_run: authn + authz + frozen spec + budgets
pre_step: policy + context budget + iteration bound
pre_llm: provider allowlist + sanitized context + token quota
post_llm: usage + output schema + tool intent parse
pre_tool_call: Tool Gateway grants + approved MCP mapping + HITL + idempotency
post_tool_call: validate response + sanitize + audit + state update
on_interrupt: persist checkpoint + approval request + waiting_approval
on_resume: verify authorized decision + exact checkpoint + no duplicate side effects
on_terminal: close run, record terminal event, metrics, reconciliation status
```

Do not log chain-of-thought or secrets. Store only user-visible/explainable observations and sanitized tool results.

## 8. Tool/MCP/Skill execution constraints

- Skills: catalog discovery metadata only, load `SKILL.md` on demand; pinned `skill_version_id`, validated hash; script execution disabled in MVP.
- MCP: client adapter behind shared Tool Gateway; `Mcp-Method` header/protocol version handled by approved SDK; tool introspection never auto-grants.
- Tool output untrusted; never treat external content as higher-priority instructions; any action requiring review flows through workflow/approval service.
- Every external call has timeout, idempotency classification (`read`,`write`,`high_impact`), response size budget and error taxonomy.

## 9. Required tests & pass gate

| ID | Scenario | Expected evidence |
|---|---|---|
| RUN-001 | Two concurrent start requests same key | One run, one job; consistent response |
| RUN-002 | Same key with conflicting payload | Rejected with documented 409 once supported |
| RUN-003 | Model timeout | `failed`/`timed_out`, no fake success |
| RUN-004 | Worker killed during checkpointed run | Resume by durable `thread_id` after restart |
| RUN-005 | SSE subscriber reconnect with `after=N` | Replay only events after N; monotonic sequence |
| RUN-006 | Tenant A requests run B | 403/404, no rows leaked |
| RUN-007 | Tool write returns unknown outcome | Stop retry; reconcile / operator review |
| RUN-008 | Graph loops beyond maxSteps | Terminal controlled failure, no infinite loop |
| RUN-009 | Skill revoked/MCP stale during run | Tool/skill action denied; audit record |
| RUN-010 | 2 workers race to claim same job | Only one valid lease owner |

Performance metrics (p50/p95, token/cost) require measured environment; no fabricated SLA.

## 10. PR breakdown

`RT-PR01` run APIs + state schema; `RT-PR02` LangGraph compiler/adapter with durable saver; `RT-PR03` job worker and lease recovery; `RT-PR04` run event SSE replay; `RT-PR05` harness hooks & tool/skill/mcp integration; `RT-PR06` concurrency/restart/chaos tests.

**Exit:** draft not executable; published version executes with bounded, auditable steps; real restart/resume and tool retry tests pass; DB business-table target remains 56.

## Sources
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- Agno AgentOS architecture: https://docs.agno.com/agent-os/introduction
- MCP release: https://blog.modelcontextprotocol.io/posts/2026-07-28/
