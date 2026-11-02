# SPEC-WORKFLOW-001 — Workflow DSL, Compiler, HITL, Recovery & Digital Worker

**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **DB:** `DB-BASELINE-56` · **Priority:** P0 · **Owner:** Control API (definition/approval), Runtime (execution)

## 1. Goal and non-goals

Enable a published WorkflowVersion to execute deterministic/business-defined steps, agent nodes and approved tool calls; preserve progress across restart; require human decision for restricted operations; cancel/retry/reconcile with audit. Digital Worker is a business identity/configuration bound to a published AgentVersion, not a new unrestricted autonomous principal.

Non-goals: complex BPMN engine, arbitrary user-provided Python execution, uncontrolled agent-generated graphs, separate workflow database/service, universal exactly-once side effects, any new domain table outside baseline 56 without ADR.

## 2. DB 56 table ownership

| Table | Owner | Invariant |
|---|---|---|
| `catalog.workflows` | Control API | Mutable `draft_spec`, optimistic `revision` |
| `catalog.workflow_versions` | Control API | Immutable `spec`, `sha256`, `version` |
| `catalog.agent_versions` | Agent Registry | Pins optional workflow version; immutable |
| `catalog.digital_workers` | Control API | Points to agent and pinned default version; status active/paused/disabled |
| `catalog.business_processes` | Control API | Business goal, constraints and examples, not code execution |
| `runtime.runs` | Runtime | Exact run state / pinned versions |
| `runtime.run_steps` | Runtime | Per-step attempt state |
| `runtime.checkpoint_refs` | Runtime | Recoverable checkpoint pointer |
| `runtime.approval_requests` | Approval service | Pending/approved/rejected/expired/cancelled |
| `runtime.approval_decisions` | Approval service | One decision per request; unique key |
| `runtime.jobs` | Runtime | Durable scheduled resume job, lease |
| `runtime.tool_operations` | Gateway | Side effect receipt and outcome |
| `runtime.run_events` | Runtime | Timeline + SSE replay |
| `governance.audit_events`, `integration.outbox_events` | Governance/Integration | Evidence and reliable notification |

`catalog.agent_skill_bindings` pin approved Skills; MCP tool is `catalog.agent_tool_bindings` + `integration.mcp_tool_mappings`; this Workflow spec must not create a separate capability system.

## 3. WorkflowSpec v1 (JSON DSL) and compiler

```json
{
  "schemaVersion": "1.0",
  "key": "quotation-approval",
  "entry": "draft",
  "steps": [
    {"id":"draft","type":"agent","agentVersionRef":"approved-agent-version","next":"review"},
    {"id":"review","type":"approval","approvalPolicyRef":"quotation-approver","next":{"approved":"send","rejected":"stop"}},
    {"id":"send","type":"tool","toolVersionRef":"approved-mock-tool-version","next":"done"},
    {"id":"stop","type":"end","outcome":"rejected"},
    {"id":"done","type":"end","outcome":"succeeded"}
  ],
  "limits":{"maxSteps":12,"maxRetriesPerStep":2,"runTimeoutSeconds":86400}
}
```

This is a sample, not complete schema; final canonical JSON Schema belongs in `contracts/jsonschema/workflow-spec.v1.schema.json` and must match immutable `workflow_versions.spec`. Reject duplicate step IDs, missing entry, invalid edges, unreachable steps, cycles without explicit bounded loop policies, unsupported step types, missing approvals for high-impact side effects, and cross-workspace references.

MVP types: `agent`, `tool`, `condition` (deterministic validated expression only), `approval`, `end`. Parallel/loop/subworkflow are phased features after compiler and checkpoint tests. `fixed_plan` business processes should compile to deterministic bounded steps; planner nodes generate **suggestions** that must be validated against preapproved bounded graph schema before execution.

## 4. State machine — use existing CHECK values

Run `queued -> running -> waiting_approval -> running -> succeeded` or terminal `failed/cancelled/timed_out`. Rejected approval is `approval_requests.status='rejected'`; run may map rejection to `succeeded` (a completed controlled rejection) or `cancelled` based on explicit WorkflowSpec semantics. **Choose and test one policy**; don't silently invent `run.status='rejected'` because existing table does not allow it.

Approval request allowed states: `pending`, `approved`, `rejected`, `expired`, `cancelled`. Approval decisions table only supports `approved` and `rejected`. Expiry is a lifecycle transition without a fabricated human decision row. `run_steps.status`: `pending`,`running`,`waiting`,`succeeded`,`failed`,`skipped`.

## 5. HITL exact action semantics

1. Before protected tool call, form canonical immutable action intent: tool_version_id, connection identity, sanitized arguments, request hash, principal, run/step, risk and target environment.
2. Compute `action_hash = sha256(canonical_intent)`. Secret values must not be embedded in the intent.
3. Create pending `runtime.approval_requests` with run/step/action_hash, role, expiry, revision; save durable checkpoint **before acknowledging** pending state.
4. Return minimal approval preview to authorized reviewer; hide secret and irrelevant user data.
5. `POST /api/v1/approvals/{approval_id}/decisions` authenticates reviewer, verifies current membership + role, separation-of-duties if configured, pending state, expiry and expected revision.
6. In one DB transaction: lock approval row; update status from pending; insert exactly one `runtime.approval_decisions`, create `runtime.jobs` resume (if approved) or terminal/rejected handling, audit + outbox; duplicate same decision returns idempotent result, conflicting response returns 409.
7. Resume job reloads exact `checkpoint_ref`, verifies run nonterminal and **re-validates action_hash, current grants, MCP mapping, Skill status and relevant resources**; only then Tool Gateway may execute.
8. If denied/rejected/expired: do not call tool; emit corresponding run event and safe user-facing explanation.

`approval_requests` unique `(tenant,workspace,run,step_key,action_hash)` and `approval_decisions` unique `(tenant,workspace,approval_request_id)` enforce part of exactly-once *decision recording*. They do not guarantee exactly-once downstream tool execution, hence `runtime.tool_operations` idempotency + reconciliation.

## 6. Crash and concurrency matrix

| Crash or race point | Recovery behavior |
|---|---|
| After approval request commit, before SSE send | UI replays from `run_events` |
| Before checkpoint persisted | Do not expose approval as ready; retry/repair inconsistent pending state |
| Reviewer A/B approve concurrently | One row wins lock; unique decision constraint; second sees conflict or replay |
| Approval expires as reviewer acts | Check DB current time in locked transaction; apply single result |
| Worker dies just after approved decision | Resume job leased by another worker; exact checkpoint |
| Tool response lost after remote write | Mark operation `unknown`, reconciliation; no blind repeat |
| Tool grant revoked during pending approval | Resume denies execution despite approved decision |
| MCP tool schema changed after publication | Mapping `stale`, deny; require re-review |
| WorkflowVersion re-published meanwhile | Existing run continues pinned version; no mutation of old graph |

## 7. API and SSE contracts

| Endpoint | Authz | Behavior |
|---|---|---|
| `POST /api/v1/workspaces/{id}/workflows` | `workflow:create` | New draft |
| `PATCH /api/v1/workspaces/{id}/workflows/{workflow_id}` | `workflow:edit` + revision | Update draft |
| `POST /api/v1/workspaces/{id}/workflows/{workflow_id}/validate` | `workflow:edit` | Static compiler diagnostics |
| `POST /api/v1/workspaces/{id}/workflows/{workflow_id}/publish` | `workflow:publish` | Immutable version |
| `GET /api/v1/workspaces/{id}/approvals?status=pending` | `approval:read` | Authorized inbox |
| `POST /api/v1/workspaces/{id}/approvals/{approval_id}/decisions` | `approval:decide` | Atomic decision (If-Match/idempotency) |
| `GET /api/v1/runs/{run_id}/events?after={sequence}` | `run:read` | SSE ordered redacted events |

SSE events: `run.started`, `step.started`, `approval.requested`, `approval.decided`, `run.resumed`, `tool.completed`, `run.completed`, `run.failed`. Payload must include `runId`, `sequence`, `eventType`, `createdAt`, `traceId` and a sanitized data field. `Last-Event-ID` handling can map to sequence; client must dedup.

## 8. File changes — no duplicate services

```text
contracts/jsonschema/workflow-spec.v1.schema.json
contracts/jsonschema/approval-decision.v1.schema.json
apps/control-api/src/control_api/modules/workflows/
  api/{router.py,schemas.py}
  application/{create.py,validate.py,publish.py}
  domain/{rules.py,compiler_diagnostics.py}
  infrastructure/{repository.py,orm_models.py}
apps/control-api/src/control_api/modules/approvals/
  api/{router.py,schemas.py}
  application/{request_approval.py,decide_approval.py,expire_approval.py}
  infrastructure/{repository.py,orm_models.py}
apps/agent-runtime/src/agent_runtime/
  compilation/{workflow_compiler.py,node_registry.py}
  execution/{run_coordinator.py,step_executor.py,resume_run.py}
  persistence/{checkpoint.py,job_repository.py}
  tools/gateway.py
apps/studio-web/src/features/{workflow-builder,approvals,run-monitor}/
```

Only create files used in the given PR. `approve` is NOT a LangGraph node making authorization decisions; the node only **interrupts**, while Control API does verified decision. If actual repo uses different layout, document equivalent without creating a parallel module.

## 9. Test matrix + gates

| ID | Fixture | PASS |
|---|---|---|
| WF-001 | Valid WorkflowSpec compile | Deterministic graph + stable hash |
| WF-002 | Missing node, unreachable node, unsafe cycle | Validation error before publish |
| WF-003 | High-impact tool without approval step | Denied publish or runtime gate |
| WF-004 | Approval pending and server restarts | Approval and exact checkpoint recoverable |
| WF-005 | Two reviewers act at once | Only one decision row, no duplicate tool |
| WF-006 | Expired request | Cannot approve, no resume tool |
| WF-007 | Rejected request | Tool never executes; user result follows policy |
| WF-008 | Approved action payload altered | action hash mismatch, deny and audit |
| WF-009 | Cancel while waiting | no late resume after terminal |
| WF-010 | Tool write times out after send | `unknown`, reconciliation, no blind repeat |
| WF-011 | MCP mapping stale after approval | resume denies |
| WF-012 | Skill revoked during wait | loader refuses new load; consistent policy |
| WF-013 | Wrong tenant/workspace attempts approve | 403/404; zero leakage |
| WF-014 | SSE reconnect | monotonic events; no missing persisted history |

## 10. PR plan & acceptance

`WF-PR01`: WorkflowSpec schema/validator/compilation; `WF-PR02`: draft/version endpoints and immutability; `WF-PR03`: checkpoint + interrupt/resume; `WF-PR04`: Approval application atomic decision and role checks; `WF-PR05`: Tool Gateway action-hash/idempotency; `WF-PR06`: SSE, restart/replay + chaos/e2e tests.

**Exit gate:** an AgentVersion with WorkflowVersion can execute an approval workflow end-to-end and survive actual process restart, no double writes and no cross-workspace approval. Baseline remains 56 business tables.

## References
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- Agno AgentOS approval APIs: https://docs.agno.com/agent-os/using-the-api
