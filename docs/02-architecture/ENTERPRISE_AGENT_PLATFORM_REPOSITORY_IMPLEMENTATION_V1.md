# Enterprise AI Agent Platform — Repository & Implementation Architecture v1.0

**Ngày:** 2026-10-30  
**Phạm vi:** Dự án độc lập học từ FPT AI Agents (Studio, Persona, Task Agent, Knowledge, Business Process, Tools, Guardrails) và FPT AI EOS (Digital Workers, Workflow, Approval, Governance).  
**Nền lý thuyết:** Enterprise Agent Platform Blueprint v1, Theory Integration v1, chỉ mục VinAI do người dùng cung cấp.  
**Trạng thái:** Thiết kế **đề xuất**; bản v1 bổ sung catalog từng file, contract, database, API, security, test gates và PR. Chưa phải source code đã xây dựng; không khẳng định là cấu trúc nội bộ của FPT.

## 0. Architecture Decision Record sơ bộ

- **ADR-001 — Monorepo:** một repo chứa UI TypeScript, Python APIs và shared contracts. Sử dụng `pnpm` workspaces cho phần TS, `uv` workspace cho Python; `turbo` có thể dùng cho frontend nhưng không phải trình quản lý Python.
- **ADR-002 — Modular monolith + separate execution:** `control-api` (configuration/identity/governance) là modular monolith; `agent-runtime` là tiến trình độc lập để cách ly workload, scaling và dependency model; **không** tách 15 microservices ngay MVP.
- **ADR-003 — Runtime abstraction:** PoC so sánh LangGraph/Agno; chọn runtime sau benchmark. Ví dụ dưới đây dùng LangGraph làm adapter ban đầu, không khóa DSL vào framework.
- **ADR-004 — Schema-first Studio:** UI phát hành `AgentSpec`/`WorkflowSpec` dạng JSON theo hợp đồng có version; server validate, kiểm tra quyền, compile; không thực thi Python/JS tự do do UI sinh ra.
- **ADR-005 — Trust boundaries:** Authentication và quyền ở API/tool gateway; `prompt` không phải biện pháp authorization. Secrets chỉ lưu dưới dạng reference, không đưa vào spec/export.
- **ADR-006 — Durable state:** phân biệt workflow checkpoint, conversation history, long-term memory, knowledge; idempotency cho side effects. Nếu sau PoC workflow dài hạn cần tính năng vượt khả năng queue+checkpointer, đánh giá Temporal riêng.

## 1. Cây thư mục đề xuất toàn diện

```text
enterprise-agent-platform/
├── README.md                          # Giới thiệu, quickstart, kiến trúc cấp cao
├── AGENTS.md                          # Quy tắc an toàn cho coding agents góp code
├── AGENT_CODING_RULES.md              # Naming, layering, testing, errors, logging
├── CONTRIBUTING.md                    # PR checklist, convention, quality gates
├── SECURITY.md                        # Threat reporting, secret handling
├── LICENSE
├── .gitignore
├── .editorconfig
├── .env.example                       # Chỉ placeholders; tuyệt đối không có secrets
├── Makefile                           # dev-up, migrate, test, lint, eval-smoke
├── package.json                       # Root scripts cho TS
├── pnpm-workspace.yaml                # apps/studio-web, packages/*
├── pnpm-lock.yaml
├── turbo.json                         # Tùy chọn: TS task orchestration
├── pyproject.toml                     # uv workspace: Python apps/libs
├── uv.lock
│
├── apps/
│   ├── studio-web/                    # Next.js App Router + TypeScript
│   │   ├── package.json
│   │   ├── next.config.ts
│   │   ├── tsconfig.json
│   │   ├── src/
│   │   │   ├── app/
│   │   │   │   ├── layout.tsx
│   │   │   │   ├── (auth)/login/page.tsx
│   │   │   │   └── (console)/w/[workspaceId]/
│   │   │   │       ├── layout.tsx
│   │   │   │       ├── overview/page.tsx
│   │   │   │       ├── agents/page.tsx
│   │   │   │       ├── agents/[agentId]/edit/page.tsx
│   │   │   │       ├── agents/[agentId]/playground/page.tsx
│   │   │   │       ├── business-processes/page.tsx
│   │   │   │       ├── workflows/page.tsx
│   │   │   │       ├── workflows/[workflowId]/edit/page.tsx
│   │   │   │       ├── knowledge/page.tsx
│   │   │   │       ├── tools/page.tsx
│   │   │   │       ├── digital-workers/page.tsx
│   │   │   │       ├── runs/page.tsx
│   │   │   │       ├── runs/[runId]/page.tsx
│   │   │   │       ├── approvals/page.tsx
│   │   │   │       ├── evaluations/page.tsx
│   │   │   │       └── settings/page.tsx
│   │   │   ├── features/
│   │   │   │   ├── agent-builder/
│   │   │   │   │   ├── components/AgentEditor.tsx
│   │   │   │   │   ├── components/PersonaForm.tsx
│   │   │   │   │   ├── components/ToolSelector.tsx
│   │   │   │   │   ├── hooks/useAgentDraft.ts
│   │   │   │   │   └── api/agentApi.ts
│   │   │   │   ├── business-processes/
│   │   │   │   ├── workflow-builder/
│   │   │   │   │   ├── components/WorkflowCanvas.tsx
│   │   │   │   │   ├── nodes/AgentNode.tsx
│   │   │   │   │   ├── nodes/ApprovalNode.tsx
│   │   │   │   │   └── adapters/graphToWorkflowSpec.ts
│   │   │   │   ├── knowledge/
│   │   │   │   ├── tool-registry/
│   │   │   │   ├── run-monitor/
│   │   │   │   ├── approvals/
│   │   │   │   └── evaluations/
│   │   │   ├── components/             # Chỉ UI chung của app
│   │   │   ├── lib/                    # Auth/session, HTTP, env, SSE client
│   │   │   └── styles/
│   │   └── tests/
│   │
│   ├── control-api/                   # FastAPI, modular monolith
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/control_api/
│   │   │   ├── main.py                 # create_app(), router registration
│   │   │   ├── core/
│   │   │   │   ├── settings.py
│   │   │   │   ├── database.py
│   │   │   │   ├── auth.py
│   │   │   │   ├── principal.py
│   │   │   │   ├── dependencies.py
│   │   │   │   └── exception_handlers.py
│   │   │   ├── modules/
│   │   │   │   ├── identity/             # OIDC identity, service accounts
│   │   │   │   ├── workspaces/           # Tenants, membership, RBAC
│   │   │   │   ├── agents/               # AgentSpec draft, version, publish
│   │   │   │   ├── business_processes/   # Goals, examples, steps, constraints
│   │   │   │   ├── workflows/            # WorkflowSpec draft, validate, publish
│   │   │   │   ├── models/               # Allowed model registry/config refs
│   │   │   │   ├── tools/                # Allowed tool registry + schemas
│   │   │   │   ├── knowledge/            # Knowledge metadata/ACL/version
│   │   │   │   ├── policies/             # Policy assignments and evaluations
│   │   │   │   ├── approvals/            # Approval lifecycle + atomic decisions
│   │   │   │   ├── deployments/          # Immutable releases + rollback target
│   │   │   │   ├── digital_workers/      # P2: assignment & work inbox metadata
│   │   │   │   ├── operations/           # Run read-model / dashboards
│   │   │   │   └── audit/                # Append-only logical audit events
│   │   │   └── integrations/
│   │   │       ├── runtime_client.py
│   │   │       └── event_publisher.py
│   │   ├── migrations/               # Alembic; schema `control`
│   │   └── tests/
│   │
│   ├── agent-runtime/                 # FastAPI, independent execution process
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/agent_runtime/
│   │   │   ├── main.py
│   │   │   ├── api/
│   │   │   │   ├── runs.py             # Submit/get/cancel/resume run
│   │   │   │   ├── sessions.py
│   │   │   │   ├── events.py           # SSE per run
│   │   │   │   └── health.py
│   │   │   ├── application/
│   │   │   │   ├── run_service.py
│   │   │   │   ├── resume_service.py
│   │   │   │   └── spec_resolver.py   # Chỉ chạy immutable published spec
│   │   │   ├── compilation/
│   │   │   │   ├── spec_validator.py
│   │   │   │   ├── workflow_compiler.py
│   │   │   │   └── node_registry.py
│   │   │   ├── engines/
│   │   │   │   ├── base.py            # RuntimeAdapter protocol/interface
│   │   │   │   └── langgraph_adapter.py
│   │   │   ├── agents/
│   │   │   │   ├── react_agent.py
│   │   │   │   ├── task_agent.py
│   │   │   │   └── bounded_planner.py
│   │   │   ├── execution/
│   │   │   │   ├── state.py
│   │   │   │   ├── transitions.py
│   │   │   │   ├── step_executor.py
│   │   │   │   └── limits.py         # Max steps, wall time, token budget
│   │   │   ├── tools/
│   │   │   │   ├── dispatcher.py
│   │   │   │   ├── request_validator.py
│   │   │   │   ├── authorization.py
│   │   │   │   ├── idempotency.py
│   │   │   │   └── mcp_client.py     # Chỉ những tools allowlisted
│   │   │   ├── context/
│   │   │   │   ├── context_builder.py
│   │   │   │   ├── token_budget.py
│   │   │   │   └── compaction.py
│   │   │   ├── memory/
│   │   │   │   ├── conversation_store.py
│   │   │   │   └── long_term_store.py
│   │   │   ├── knowledge/
│   │   │   │   ├── retriever.py
│   │   │   │   ├── reranker.py
│   │   │   │   └── citation_validator.py
│   │   │   ├── llm/
│   │   │   │   ├── gateway.py
│   │   │   │   ├── provider_adapters.py
│   │   │   │   └── usage_meter.py
│   │   │   ├── safety/
│   │   │   │   ├── input_guard.py
│   │   │   │   ├── output_guard.py
│   │   │   │   └── pii_filter.py
│   │   │   ├── persistence/
│   │   │   │   ├── run_repository.py
│   │   │   │   ├── checkpoint_store.py
│   │   │   │   └── tool_receipt_store.py
│   │   │   └── observability/
│   │   │       ├── telemetry.py
│   │   │       └── run_events.py
│   │   ├── migrations/              # Nếu runtime sở hữu tables độc lập
│   │   └── tests/
│   │
│   └── jobs-worker/                  # P1: async ingestion/evals; KHÔNG cần MVP ngày 1
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── src/jobs_worker/
│       │   ├── main.py
│       │   ├── jobs/ingest_document.py
│       │   ├── jobs/rebuild_index.py
│       │   └── jobs/run_evaluation.py
│       └── tests/
│
├── packages/                          # Chỉ các TS packages có nhu cầu tái sử dụng
│   ├── ui/                             # Design system
│   ├── api-client/                     # TS client GENERATED từ OpenAPI
│   └── config/                         # Shared TS config (eslint/tsconfig)
│
├── libs/python/                       # Python reusable libraries; uv workspace
│   ├── platform-common/
│   │   ├── pyproject.toml
│   │   └── src/platform_common/
│   │       ├── principal.py           # Authenticated Principal, tenant context
│   │       ├── errors.py
│   │       ├── correlation.py
│   │       └── events.py
│   └── agent-contracts/
│       ├── pyproject.toml
│       └── src/agent_contracts/
│           ├── agent_spec.py
│           ├── workflow_spec.py
│           ├── tool_spec.py
│           └── run_event.py
│
├── contracts/                          # Canonical cross-language contracts
│   ├── jsonschema/
│   │   ├── agent-spec.v1.schema.json
│   │   ├── workflow-spec.v1.schema.json
│   │   ├── tool-spec.v1.schema.json
│   │   └── run-event.v1.schema.json
│   ├── openapi/
│   │   ├── control-api.openapi.yaml
│   │   └── runtime-api.openapi.yaml
│   ├── events/
│   │   └── run-events.md
│   └── compatibility.md                # Version skew & backward compatibility
│
├── policies/
│   ├── README.md
│   ├── role-permissions.yaml
│   ├── tool-risk-levels.yaml
│   ├── approval-rules.yaml
│   └── rego/                           # P2: chỉ dùng khi chọn OPA
│       └── tool_access.rego
│
├── infra/
│   ├── compose/docker-compose.dev.yml  # Postgres, Qdrant, MinIO, APIs, UI
│   ├── postgres/init.sql
│   ├── otel/collector.yaml
│   ├── monitoring/alerts.yaml
│   ├── seed/demo-seed.py
│   └── deploy/                          # P2: Helm/Terraform tùy yêu cầu triển khai
│
├── tests/                               # Cross-app suites
│   ├── contract/
│   │   ├── test_agent_spec_roundtrip.py
│   │   └── test_openapi_compatibility.py
│   ├── e2e/
│   │   ├── test_agent_publish_and_run.py
│   │   └── test_quote_approval_flow.py
│   ├── security/
│   │   ├── test_cross_tenant_isolation.py
│   │   ├── test_tool_scope_enforcement.py
│   │   └── test_prompt_injection_boundary.py
│   └── recovery/
│       ├── test_resume_after_restart.py
│       └── test_duplicate_approval_no_double_action.py
│
├── evals/
│   ├── datasets/golden_tasks.v1.jsonl
│   ├── datasets/adversarial_inputs.v1.jsonl
│   ├── rubrics/tool_use.md
│   ├── rubrics/grounding.md
│   ├── runners/evaluate_agents.py
│   └── reports/.gitkeep
│
├── examples/
│   ├── knowledge-assistant/
│   │   ├── agent-spec.json
│   │   └── sample-questions.jsonl
│   └── quotation-approval/
│       ├── agent-spec.json
│       ├── workflow-spec.json
│       ├── mock-tool-responses.json
│       └── README.md
│
├── docs/
│   ├── product/PRD.md
│   ├── product/ROLE_MATRIX.md
│   ├── architecture/SYSTEM_CONTEXT.md
│   ├── architecture/CONTAINER_DIAGRAM.md
│   ├── architecture/BOUNDED_CONTEXTS.md
│   ├── architecture/SEQUENCE_RUN_APPROVAL.md
│   ├── architecture/TRUST_BOUNDARIES.md
│   ├── architecture/DATA_OWNERSHIP.md
│   ├── architecture/STATE_MACHINE.md
│   ├── architecture/CONTRACT_VERSIONING.md
│   ├── adr/0001-monorepo-modular-monolith.md
│   ├── adr/0002-runtime-adapter.md
│   ├── adr/0003-declarative-agent-spec.md
│   ├── api/README.md
│   ├── security/THREAT_MODEL.md
│   ├── security/DATA_CLASSIFICATION.md
│   ├── runbooks/APPROVAL_RECOVERY.md
│   ├── runbooks/LLM_PROVIDER_OUTAGE.md
│   └── roadmap/IMPLEMENTATION_PHASES.md
│
└── .github/
    ├── workflows/ci-web.yml
    ├── workflows/ci-python.yml
    ├── workflows/contracts-and-security.yml
    ├── workflows/evals-smoke.yml
    └── pull_request_template.md
```

**Ghi chú:** Cây trên là **target structure**; không cần tạo mọi file rỗng từ ngày đầu. Đường dẫn `jobs-worker/`, `policies/rego/`, `infra/deploy/` và những tích hợp phức tạp được trì hoãn cho đến khi có use case chứng minh.

## 2. Quy tắc phân chia source

### 2.1 Control API (`apps/control-api`)

Một module thực tế, ví dụ `modules/agents/`:

```text
agents/
├── router.py          # HTTP endpoints; không có SQL, không compile workflow
├── schemas.py         # Request/response DTOs
├── models.py          # Database entities
├── repository.py      # Truy cập database chỉ module này sở hữu
├── service.py         # Business rules + authorization checks
├── versioning.py      # Draft, immutable version, publish, rollback reference
└── tests/
    ├── test_agents_service.py
    └── test_agent_publish_api.py
```

Theo domain ownership: `agents` không ghi trực tiếp bảng `approvals`; `approvals` chỉ kiểm soát approval, `workspaces` kiểm soát membership, `operations` là projection đọc; thay đổi qua API/service interface hoặc domain event. Khởi đầu có thể dùng *một Postgres instance* với schema/table được phân quyền, không cần nhiều database server.

### 2.2 Runtime (`apps/agent-runtime`)

Hướng dependency: `api -> application -> compilation/engines -> execution -> ports -> adapters`; không cho `domain` import FastAPI/SQLAlchemy. Tối thiểu cần `run_service`, `spec_validator`, `workflow_compiler`, `langgraph_adapter`, `dispatcher`, `checkpoint_store`, `authorization` và `telemetry`. `langgraph_adapter` phải nằm ở lớp integration, không lan vào JSON contract.

### 2.3 Studio (`apps/studio-web`)

Trang route chỉ làm composition/routing. Mọi UI/logic của một feature ở `features/<feature>/`. `WorkflowCanvas.tsx` đổi graph từ UI thành `WorkflowSpec` qua adapter; API luôn validate lại server-side. Không lưu API key LLM vào trình duyệt.

## 3. File nào là SOURCE OF TRUTH?

| Trách nhiệm | File / Nguồn chuẩn | Không nên để ở |
|---|---|---|
| Agent structure | `contracts/jsonschema/agent-spec.v1.schema.json` | UI state shape tự phát minh |
| Workflow DSL | `contracts/jsonschema/workflow-spec.v1.schema.json` | `WorkflowCanvas.tsx` tự định nghĩa DSL |
| API contract | `contracts/openapi/*.openapi.yaml` (generated & checked) | Docs viết tay không đồng bộ |
| Tool permissions | `policies/*.yaml` + authorized grants trong DB | System prompt |
| Published agent | DB `agent_versions` immutable (spec + checksum) | File chỉnh thủ công khi chạy |
| Long-lived execution state | DB/checkpointer owned by runtime | In-memory Python dict |
| User semantic memory | Memory store có `tenant_id`, `owner_id`, consent, TTL | Checkpoint dùng chung |
| Document contents | Object storage; DB metadata/source status | Chỉ Qdrant |
| Vector index | Qdrant chỉ mục, có version/ACL metadata | Thay thế authoritative metadata |
| Secrets | External secret manager / env injection | JSON AgentSpec hoặc `.env` commit |
| Approval decisions | Atomic durable DB approval record | Chat message "approved" |
| Audit | Append-only auditable events with actor/action/time/outcome | Chỉ stdout logs |

## 4. API và event contract tối thiểu

```text
Control API:
POST   /v1/workspaces/{wid}/agents
POST   /v1/workspaces/{wid}/agents/{aid}/versions
POST   /v1/workspaces/{wid}/agents/{aid}/publish
GET    /v1/workspaces/{wid}/tools
POST   /v1/workspaces/{wid}/knowledge/documents
POST   /v1/workspaces/{wid}/workflows/{id}/publish
GET    /v1/workspaces/{wid}/approvals
POST   /v1/workspaces/{wid}/approvals/{id}/decisions
GET    /v1/workspaces/{wid}/audit-events

Runtime API (internal/user gateway as authorized):
POST   /v1/runs
GET    /v1/runs/{run_id}
GET    /v1/runs/{run_id}/events   # SSE; reconnect via event IDs
POST   /v1/runs/{run_id}/cancel
POST   /v1/runs/{run_id}/resume   # internal trusted approval signal
```

Điểm quan trọng: mỗi API có authenticate principal, xác minh `workspace_id` và resource ownership. `resume` không nhận một boolean tùy ý từ client; phải tham chiếu approval đã kiểm tra chữ ký/quyền và trạng thái.

## 5. JSON cấu hình Agent minh họa (không phải schema FPT hoặc Agno)

```json
{
  "schemaVersion": "1.0",
  "name": "Quotation Assistant",
  "mode": "fixed_plan",
  "modelRef": "models/approved-default",
  "persona": {"instructions": "Prepare an internal quotation draft"},
  "toolRefs": ["tools/customer-read@1", "tools/quotation-draft@1"],
  "knowledgeRefs": ["knowledge/sales-guidelines@3"],
  "limits": {"maxSteps": 8, "maxInputTokens": 12000},
  "workflowRef": "workflows/quotation-approval@1"
}
```

- UI chỉ lưu tham chiếu capability; `toolRefs` phải được đăng ký và allowlisted cho principal/tenant.
- `schemaVersion` là phiên bản định dạng contract; phiên bản publish phải là **immutable revision** với checksum/ID do server cấp.
- Runtime cần nhận exact revision, không tự resolve `latest` giữa một run.
- `mode` có thể hỗ trợ `fixed_plan` ban đầu; `planner_based` chỉ thêm sau khi chứng minh bounded autonomy và policy.

## 6. Tối thiểu các bảng dữ liệu

```text
control schema:
  workspaces, users, memberships, service_accounts,
  agents, agent_versions, agent_publications,
  business_processes, workflows, workflow_versions, tool_definitions,
  tool_credential_refs, knowledge_bases, documents, document_versions,
  approval_requests, approval_decisions, audit_events

runtime schema:
  runs, run_steps, checkpoints, conversation_messages,
  memory_records, tool_execution_receipts, runtime_outbox

analytics/projections (có thể ở control schema MVP):
  run_summaries, evaluation_runs, usage_costs
```

Mọi bảng thuộc không gian tenant phải có `workspace_id` hoặc enforcement tương đương. Unique constraint cho `(workspace_id, idempotency_key)` ở tool action; kiểm tra quyền tại application và database boundary, không chỉ dựa vào UI filters.

## 7. Một luồng end-to-end mẫu

```text
Studio: Edit Agent Draft
   -> Control API: Validate AgentSpec & Tool Grants
   -> Control API: Publish immutable AgentVersion + WorkflowVersion
   -> Runtime: Start Run(tenant, principal, exact versions)
   -> Compiler: Validate DSL and build executable graph
   -> Runtime: Retrieve documents with ACL + provenance
   -> LLM: Generate draft (not external side-effect)
   -> Control API: Create ApprovalRequest and suspend
   -> Admin: Approved/Rejected with server-side auth
   -> Runtime: Resume verified checkpoint
   -> Tool Gateway: Authorize, check idempotency, execute mock API
   -> Event Store/Tracer: write outcome, usage, trace, audit
   -> Operations UI: show timeline and result
```

Nguyên tắc: **checkpoint không bảo đảm exactly-once side effects**; tool gateway cần transaction/idempotency riêng.

## 8. Các bộ kiểm thử bắt buộc

| Case | Test suite | Điều kiện PASS |
|---|---|---|
| Draft->publish->run | `tests/e2e/test_agent_publish_and_run.py` | Run gắn chính xác immutable version |
| Tamper spec | `tests/contract/test_agent_spec_roundtrip.py` | Reject schema/capability không hợp lệ |
| Cross-tenant | `tests/security/test_cross_tenant_isolation.py` | 401/403/404, không tiết lộ resource khác tenant |
| Malicious document | `tests/security/test_prompt_injection_boundary.py` | Không bypass tool policy dù nội dung RAG ra lệnh |
| Approval restart | `tests/recovery/test_resume_after_restart.py` | Resume đúng checkpoint sau restart |
| Double approval | `tests/recovery/test_duplicate_approval_no_double_action.py` | Chỉ một action được commit |
| Model unavailable | `apps/agent-runtime/tests/test_llm_gateway.py` | Fail controlled; không báo mock là kết quả thật |
| RAG ACL | `apps/agent-runtime/tests/test_retriever_acl.py` | Không truy cập tài liệu unpublished/cross-tenant |
| Tool timeout | `apps/agent-runtime/tests/test_tool_timeout.py` | Timeout bounded; không retry unsafe side effects |
| Tracing | `tests/e2e/test_trace_linkage.py` | Một `run_id` nối được LLM/tool/approval/audit |

Các ngưỡng latency/task success sẽ được chốt sau khi có benchmark; không ghi target lab như thành tích production.

## 9. Thứ tự khởi tạo file theo giai đoạn

**P0 – Skeleton (làm ngay)**
1. Root: `README.md`, `AGENTS.md`, `AGENT_CODING_RULES.md`, `.editorconfig`, `.env.example`, `Makefile`, workspaces, CI.
2. `contracts/jsonschema/{agent-spec,workflow-spec,tool-spec}.v1.schema.json` + tests.
3. `apps/control-api`: app, config, database, `workspaces`, `agents`, `tools`, auth.
4. `apps/agent-runtime`: app, run endpoint, validator, basic agent, model adapter, checkpoint, events.
5. `apps/studio-web`: login, workspace, agents, editor, playground.
6. `infra/compose/docker-compose.dev.yml` với Postgres và mock provider; Qdrant bổ sung khi Knowledge vào scope.

**P1 – Agent Builder + Knowledge**
- `business_processes` (mục tiêu, câu mẫu, hướng dẫn, giới hạn) liên kết `AgentSpec` và `WorkflowSpec`.
- `features/knowledge`, backend knowledge module, ingest job, retrieval, citations, generated client.
- `versioning.py`, `deployments`, playground eval fixtures.

**P2 – AgentOS operations**
- `workflow-builder`, `approvals`, `runs`, `policies`, `tool_execution_receipts`, checkpoint recovery, cross-app traces.
- Worker nếu ingestion/eval/timeout tasks không nên ở web request process.

**P3 – Enterprise hardening**
- Multi-agent patterns sau benchmark, advanced scheduling, optional OPA/Temporal, broader MCP/A2A, tenant limits, deployment automation, cost controls, disaster recovery.

## 10. 10 quy tắc code review không được phá vỡ

1. API routes không được chứa business logic hoặc truy cập LLM trực tiếp.
2. Contracts có `schemaVersion` và backward-compatibility checks.
3. `AgentSpec` được lưu draft/publish; immutable revision trong run.
4. Cross-tenant resource access bị từ chối ở **backend**.
5. Tool schemas được validate trước gọi; policy sau xác thực principal, trước side effect.
6. Dynamic Python/JS trong no-code AgentSpec không được thực thi tùy ý.
7. Không đưa credential và PII vào prompts/traces/log mặc định.
8. Checkpoint, memory và RAG dùng persistence/ownership khác nhau.
9. Tool side effects có idempotency và auditable receipts.
10. PR phải có unit/contract/security/eval tests phù hợp, không fake metrics hoặc fake provider responses như live.

## 11. Tài liệu xác minh và ảnh hưởng đến thiết kế

- FPT AI Agents — https://docs.fpt.ai/docs/vi/aiagents/documentation/aiagent/ (chức năng persona, business process, knowledge, tools, guardrails)
- FPT AI EOS — https://fpt.ai/vi/tin-tuc/trinh-lang-fpt-ai-eos-fpt-ai-mo-ra-cach-thuc-van-hanh-moi-voi-nhan-su-so/ (digital worker, governance, approval)
- Agno Studio — https://docs.agno.com/agent-os/studio/introduction (Agent/Team/Workflow UI)
- Agno Registry — https://docs.agno.com/agent-os/studio/registry (registered components vs dynamic UI)
- LangGraph Persistence — https://docs.langchain.com/oss/python/langgraph/persistence (checkpoint vs store)
- FastAPI Bigger Applications — https://fastapi.tiangolo.com/tutorial/bigger-applications/ (routers/dependencies)
- Turborepo Repository — https://turborepo.com/docs/crafting-your-repository/structuring-a-repository (JS workspaces apps/packages)
- Temporal Workflow Execution — https://docs.temporal.io/workflow-execution (durable jobs when necessary)
- Open Policy Agent — https://www.openpolicyagent.org/docs (policy decision/enforcement split)
- OpenTelemetry GenAI — https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ (LLM/tool instrumentation and sensitive attributes)

**Phân biệt bằng chứng:** các nguồn xác thực từng nguyên tắc/framework, còn sơ đồ repository, filename và tech choices là đề xuất thiết kế mới. Không có bằng chứng rằng FPT sử dụng chính xác cấu trúc thư mục này.

---

# PHẦN II — Implementation-Ready Design (mở rộng v1.0)

> Phần I ở trên là *target repository*. Phần II chuyển từng nhánh thành chỉ dẫn lập trình, hợp đồng dữ liệu, quy tắc phụ thuộc và test. **Đây là tài liệu thiết kế, không phải repository chạy sẵn.** Chưa chốt lựa chọn runtime cho production trước PoC. Mục tiêu đầu tiên là vertical slice tạo draft → publish → run → approval → resume → audit.

## 12. Các quyết định chốt trước khi tạo file

| Quyết định | MVP | Khi nào được thay đổi |
|---|---|---|
| Đơn vị triển khai | 3 ứng dụng: Studio Web / Control API / Agent Runtime | Worker riêng khi ingestion/evaluation vượt khả năng background jobs hiện tại |
| Control plane | FastAPI modular monolith | Chỉ tách service khi domain ownership, deployment hoặc scaling đòi hỏi |
| Runtime | LangGraph Adapter thử nghiệm | Kết luận từ benchmark so với Agno, chưa hardcode framework trong DSL |
| Lưu dữ liệu | Một PostgreSQL instance, `control` và `runtime` schemas với roles khác nhau | Khi có nhu cầu vận hành và mở rộng độc lập |
| Vector index | Qdrant sau khi có Knowledge | Không dùng vector DB thay thế source documents |
| Blob storage | MinIO local / S3-compatible | Theo môi trường triển khai |
| Sessions & memory | Checkpoint + conversation + user memory lưu riêng | Chỉ bật long-term memory có cơ sở, consent, retention |
| Identity | OIDC/JWT, principal từ token đã verify | Không nhận `tenant_id`/role tự khai từ body như quyền hạn |
| Tool sandbox | Allowlisted API/MCP connector, deny-by-default | Không cho UI sinh Python/JS tùy ý trong MVP |
| Approval | DB record với atomic transition + validated approver | Không resume bằng `approved: true` thiếu ràng buộc |
| Persistence | Checkpointer bền vững + idempotency receipts | Chỉ thêm Temporal sau load/recovery benchmark |
| Events | Persistent run event log + SSE projection | Nâng cấp bus khi có multi-replica/throughput yêu cầu |

### 12.1 Ranh giới ứng dụng (deployment)

```text
Browser (public)
   │ HTTPS
   ▼
Studio Web (Next.js) ── authenticated API ──► Control API (FastAPI)
   │                                                │
   │ read stream under user authorization            │ signed service-to-service auth
   └──────────────────────────────────────────────► Agent Runtime (FastAPI)
                                                    │
                                         ┌──────────┼────────────┐
                                         ▼          ▼            ▼
                                      LLM API    Tool Gateway   RAG
                                                     │            │
                                               connector/API     Qdrant

Postgres.control: authoritative definitions, versions, approvals, audit
Postgres.runtime: run state, checkpoints, tool receipts, event stream
S3-compatible: document binaries; PostgreSQL: document metadata and ACL
```

**Invariant:** Studio không gọi LLM provider với credentials của platform. Runtime không tự sửa `agent_versions`; Control API không chạy workflow. Access token của người dùng không được gửi nguyên vẹn tới tool bên thứ ba khi không cần thiết.

### 12.2 Quy tắc import và dependency

```text
studio-web/app -> studio-web/features -> generated api-client
control-api/router -> application/service -> domain & ports
control-api/repository -> DB adapter (không import runtime internals)
agent-runtime/api -> application -> compiler/engine ports
agent-runtime/engines/langgraph_adapter -> LangGraph concrete library
agent-runtime/tool_gateway -> authorization + connector ports
shared contracts -> không import business modules
```

Cấm `domain` import `FastAPI`, `SQLAlchemy`, `httpx`, `langgraph`, `agno`. Adapter được phép import domain interfaces, chiều ngược lại không được phép. Với module nhỏ có thể gộp `domain/rules.py` vào `service.py` khi chưa có logic đủ lớn; không tạo hàng trăm file trống theo giáo điều.

## 13. Catalog file Control API — ai chịu trách nhiệm điều gì?

Đây là kiến trúc hướng đích; **MVP chỉ tạo file khi có code thực sự**. Mỗi domain có `router.py`, `service.py`, `repository.py`, `schemas.py` và `models.py` khi cần. Không nhất thiết nhân bản đầy đủ tầng cho module nhỏ.

| Module | File then chốt (đường dẫn tương đối trong `modules/`) | Business invariant và dữ liệu sở hữu |
|---|---|---|
| `identity` | `identity/router.py`, `identity/service.py`, `identity/principal.py` | OIDC subject, identities, service accounts; JWT verification không ở UI |
| `workspaces` | `workspaces/service.py`, `workspaces/repository.py`, `workspaces/policy.py` | Workspace membership, tenant-scoped grants; không tin workspace ID trong input như bằng chứng quyền |
| `agents` | `agents/api/router.py`, `agents/application/publish_agent.py`, `agents/domain/entities.py`, `agents/infrastructure/repository.py` | Agent draft; immutable `agent_versions`; publish là atomic operation |
| `business_processes` | `business_processes/service.py`, `business_processes/schemas.py` | Tên, mục tiêu, câu mẫu, bước, giới hạn; version riêng khi được reference từ AgentSpec |
| `workflows` | `workflows/validator.py`, `workflows/versioning.py`, `workflows/service.py` | WorkflowSpec không có cycle không bounded; validate references và paths |
| `models` | `models/registry.py`, `models/policy.py` | Những model được phép, quota, provider binding references, không lưu cleartext API key |
| `tools` | `tools/registry.py`, `tools/schemas.py`, `tools/grants.py` | Tool metadata, parameter schema, risk level; quyền chọn tool không đồng nghĩa quyền execute |
| `knowledge` | `knowledge/service.py`, `knowledge/documents.py`, `knowledge/permissions.py` | Documents, version, status, ACL, upload references; DB là nguồn sự thật về xuất bản |
| `policies` | `policies/service.py`, `policies/evaluator.py` | Rule assignment và policy version; deny-by-default |
| `approvals` | `approvals/router.py`, `approvals/service.py`, `approvals/repository.py` | Approval CAS transition; approval gắn đúng action payload hash và principal |
| `deployments` | `deployments/service.py`, `deployments/snapshots.py` | Immutable release bundle; pin model/tool/workflow/knowledge/policy refs |
| `digital_workers` | `digital_workers/service.py`, `digital_workers/assignments.py` | Work inbox & trách nhiệm, không phải engine chạy riêng |
| `operations` | `operations/read_model.py`, `operations/router.py` | Projection để query run timeline, cost, health; không sở hữu run state |
| `audit` | `audit/writer.py`, `audit/query.py` | Append-only event log, redaction, actor/action/target/outcome |

### 13.1 Deep dive `agents` — ví dụ một module đủ tiêu chuẩn

```text
modules/agents/
├── api/
│   ├── router.py                # HTTP: create/list/update/versions/publish
│   ├── request_dto.py           # Pydantic input, rejecting unknown fields
│   └── response_dto.py          # Pydantic output, never leaks credentials
├── application/
│   ├── create_draft.py          # Authz -> create draft
│   ├── update_draft.py          # Optimistic lock for concurrent editors
│   ├── publish.py               # Validate/resolve permissions/atomic commit
│   ├── archive.py               # Soft-delete without breaking old runs
│   └── list_agents.py           # Pagination + workspace scoping
├── domain/
│   ├── entities.py              # Agent, AgentVersion
│   ├── lifecycle.py             # DRAFT/PUBLISHED/ARCHIVED rules
│   ├── errors.py                # Conflict, invalid spec, unauthorized
│   └── events.py                # AgentPublished, AgentArchived
├── infrastructure/
│   ├── orm_models.py            # DB mapping
│   └── repository.py            # Queries always scoped by workspace
└── tests/
    ├── test_create_draft.py
    ├── test_concurrent_update.py
    ├── test_publish_constraints.py
    └── test_cross_workspace_access.py
```

**`publish.py` chính xác phải làm:** xác thực requester và role → load draft với version guard → JSON Schema validation → resolve referenced model/tool/workflow/knowledge → kiểm tra grants, limits, schema compatibility → sinh immutable bundle/hash → ghi phiên bản và publication với transaction → ghi outbox/audit record → trả `agent_version_id`. Không thực hiện LLM call trong transaction.

### 13.2 Deep dive `approvals`

```text
modules/approvals/
├── router.py                 # GET inbox, POST decision
├── schemas.py                # DecisionInput (approve/reject, reason, expected_version)
├── service.py                # Check role/SoD, check expiry, exact payload/hash
├── repository.py             # Atomic UPDATE ... WHERE status = 'PENDING'
├── events.py                 # ApprovalDecided event into outbox
└── tests/
    ├── test_approval_expired.py
    ├── test_double_approval.py
    └── test_approver_not_requester.py
```

`approve` chỉ là một **quyết định**. Runtime vẫn phải kiểm tra policy và grant ngay trước side effect. Nếu webhook gửi hai lần, dùng decision ID + operation key bền vững để không nhân đôi kết quả.

## 14. Catalog file Agent Runtime — trái tim của hệ thống

| Thành phần | Những file quan trọng | Trách nhiệm và những điều KHÔNG được làm |
|---|---|---|
| `api` | `runs.py`, `events.py`, `sessions.py`, `health.py` | Verify principal, request DTO, SSE; không nhúng prompt hoặc SQL trực tiếp |
| `application` | `run_service.py`, `resume_service.py`, `spec_resolver.py` | Exact published bundle, run commands, resume; không tự phát quyền |
| `compilation` | `spec_validator.py`, `node_registry.py`, `workflow_compiler.py` | Chuyển allowlisted declarative graph thành executable graph, giới hạn loop/path |
| `engines` | `base.py`, `langgraph_adapter.py` | Framework adapter; không đưa class LangGraph ra JSON API |
| `agents` | `react_agent.py`, `task_agent.py`, `bounded_planner.py` | ReAct/task execution có giới hạn bước/token/time |
| `execution` | `state.py`, `transitions.py`, `step_executor.py`, `limits.py` | Finite state machine, deadlines, cancellations, invariants |
| `tools` | `dispatcher.py`, `authorization.py`, `request_validator.py`, `idempotency.py`, `mcp_client.py` | Validate → policy → approval → execute; không tin tool parameters do model suy diễn |
| `context` | `context_builder.py`, `token_budget.py`, `compaction.py` | Tổng hợp prompt context theo budget và provenance |
| `memory` | `conversation_store.py`, `long_term_store.py`, `memory_policy.py` | Tách transcript/long-term facts; tenant/owner/consent/TTL |
| `knowledge` | `retriever.py`, `reranker.py`, `citation_validator.py` | ACL filters trước retrieval, source references, grounding checks |
| `llm` | `gateway.py`, `provider_adapters.py`, `usage_meter.py`, `retry_policy.py` | Rate limit, fallback thật, timeout, usage measured/estimated flag |
| `safety` | `input_guard.py`, `output_guard.py`, `pii_filter.py`, `prompt_injection.py` | Defense in depth, không thay được authorization |
| `persistence` | `run_repository.py`, `checkpoint_store.py`, `tool_receipt_store.py`, `outbox.py` | Durable run state và tool receipts; không lưu tất cả vào một memory table |
| `observability` | `telemetry.py`, `run_events.py`, `redaction.py` | Trace IDs, events, safe logs, costs, no raw secrets |

### 14.1 Cần phân biệt 5 loại state

| State | Persist ở đâu? | Ai sở hữu? | Ví dụ |
|---|---|---|---|
| Agent/Workflow definitions | `control` DB | Control API | `agent_version_id` immutable |
| Run checkpoint | `runtime` DB / LangGraph checkpointer | Runtime | Node hiện tại, nhánh workflow, interrupt |
| Conversation transcript | `runtime.messages` | Runtime | Lịch sử câu hỏi/phản hồi |
| Long-term memory | Memory store có scope/retention | Runtime | Preference được người dùng cho phép lưu |
| Knowledge/citations | Object store + metadata DB + vector index | Knowledge domain | Document version, page, chunk, ACL |

Checkpoint **không** dùng thay cho approval DB, tool receipts hoặc external transaction. LangGraph phân biệt graph checkpoints theo thread và long-term store xuyên thread (xem tài liệu LangGraph Persistence).

### 14.2 `ToolDispatcher` — thứ tự kiểm tra không thể đảo

```text
receive tool call
 -> resolve immutable tool definition/version
 -> validate input JSON Schema / Pydantic
 -> load trusted principal + workspace + run permissions
 -> check policy/grants + resource ownership
 -> check approval policy (if side effect/risk)
 -> verify decision references exact payload hash
 -> reserve durable operation_key/idempotency record
 -> call connector with timeout + bounded retry policy
 -> reconcile ambiguous outcomes (unknown timeout)
 -> write result/receipt + audit/outbox
 -> return sanitized result to Agent
```

Nếu connector không hỗ trợ idempotency hoặc status lookup, **không thể bảo đảm exactly-once đối với hệ thống bên ngoài**. Cần xử lý trạng thái `UNKNOWN` và manual reconciliation; tuyệt đối không tự động retry các side effect thiếu tính idempotent.

## 15. Frontend feature structure chi tiết

Mỗi feature dùng một convention nhất quán:

```text
features/agent-builder/
├── api/
│   ├── queries.ts             # Read via generated client
│   └── commands.ts            # Mutations via generated client
├── components/
│   ├── AgentList.tsx
│   ├── AgentEditor.tsx
│   ├── PersonaEditor.tsx
│   ├── ModelSelect.tsx
│   ├── ToolGrantPicker.tsx
│   ├── KnowledgePicker.tsx
│   └── PublishDialog.tsx
├── hooks/
│   ├── useAgentDraft.ts
│   └── useAgentPermissions.ts
├── model/
│   ├── editorState.ts         # UI-only transient state
│   └── formAdapter.ts         # Converts form <-> AgentSpec
├── validators/
│   └── validateEditor.ts      # Fast feedback; backend authoritative
└── tests/
    ├── AgentEditor.test.tsx
    ├── PublishDialog.test.tsx
    └── formAdapter.test.ts
```

Các feature khác cần file đặc thù:

| Feature | File chủ chốt | Điều kiện phải đáp ứng |
|---|---|---|
| `workflow-builder` | `nodes/*.tsx`, `edges/*.tsx`, `model/graphToSpec.ts`, `model/specToGraph.ts` | Round-trip không mất node IDs, edge semantics, typed params |
| `knowledge` | `components/DocumentUploader.tsx`, `ChunkReview.tsx`, `api/commands.ts` | Hiển thị trạng thái ingest/version, không tự tạo citation giả |
| `tool-registry` | `ToolDefinitionForm.tsx`, `PermissionMatrix.tsx` | Không bao giờ hiển thị secret plaintext |
| `agent-playground` | `ChatPane.tsx`, `useRunStream.ts`, `TraceInspector.tsx` | Có event IDs, cancellation, real vs mock indicator |
| `approvals` | `ApprovalInbox.tsx`, `DecisionDialog.tsx` | Hiện diff hành động + payload hash/expiry; stale decision rõ ràng |
| `run-monitor` | `RunTimeline.tsx`, `StepDetails.tsx`, `UsagePanel.tsx` | Khớp `run_id`, `trace_id`, event sequence |
| `evaluations` | `DatasetList.tsx`, `EvalRunDetails.tsx` | So sánh theo spec version/dataset/model settings |

**UI route không định nghĩa DSL.** Ví dụ `WorkflowCanvas.tsx` chỉ hiển thị graph; `graphToSpec.ts` phải dùng contract versioned. Những quy ước `/app/(console)/.../page.tsx`, route groups, colocation theo Next.js App Router.

## 16. Canonical contracts và versioning

### 16.1 File chính thức trong `contracts/`

```text
contracts/
├── jsonschema/
│   ├── agent-spec.v1.schema.json
│   ├── workflow-spec.v1.schema.json
│   ├── tool-spec.v1.schema.json
│   ├── policy-spec.v1.schema.json
│   ├── approval-request.v1.schema.json
│   ├── run-event.v1.schema.json
│   └── run-result.v1.schema.json
├── openapi/
│   ├── control-api.openapi.yaml    # generated & snapshot tested
│   └── runtime-api.openapi.yaml    # generated & snapshot tested
├── examples/
│   ├── quotation-agent.v1.json
│   ├── quotation-workflow.v1.json
│   ├── approval-event.v1.json
│   └── run-completed-event.v1.json
├── generated/
│   ├── python/                    # generated models; never hand-edit
│   └── typescript/                # generated client/types; never hand-edit
└── COMPATIBILITY.md
```

Chọn **JSON Schema là source-of-truth cho AgentSpec/WorkflowSpec**, phát sinh Pydantic/TS types có kiểm tra trong CI; OpenAPI được sinh từ code FastAPI rồi kiểm tra compatibility trong CI. Không vừa tự sửa OpenAPI vừa tự sửa routes mà không đồng bộ.

### 16.2 AgentSpec minh hoạ (được đề xuất, không phải định dạng FPT)

```json
{
  "schemaVersion": "1.0",
  "kind": "Agent",
  "metadata": {"name": "quotation-assistant", "displayName": "Quotation Assistant"},
  "modelRef": "model-config:approved-general@2",
  "persona": {"instructions": "Draft internal quotations only."},
  "mode": "fixed_plan",
  "toolRefs": ["tool:crm-read@1", "tool:draft-quotation@1"],
  "knowledgeRefs": ["knowledge:sales-policy@3"],
  "workflowRef": "workflow:quotation-approval@1",
  "limits": {"maxSteps": 10, "maxWallTimeSeconds": 180, "maxTotalTokens": 16000},
  "approvalPolicyRef": "policy:external-send@2"
}
```

`schemaVersion` là format, không phải phiên bản agent publish. Runtime chỉ nhận `agent_version_id` + immutable release bundle ID/hash, không lấy lại `latest` trong khi run.

### 16.3 WorkflowSpec minh hoạ

```json
{
  "schemaVersion": "1.0",
  "kind": "Workflow",
  "nodes": [
    {"id": "retrieve", "type": "knowledge_retrieval", "params": {"knowledgeRef": "knowledge:sales-policy@3"}},
    {"id": "draft", "type": "agent_step", "params": {"agentRef": "agent:quotation-drafter@2"}},
    {"id": "approval", "type": "human_approval", "params": {"policyRef": "policy:external-send@2"}},
    {"id": "deliver", "type": "tool_call", "params": {"toolRef": "tool:submit-to-sandbox@1"}}
  ],
  "edges": [
    {"from": "retrieve", "to": "draft"},
    {"from": "draft", "to": "approval"},
    {"from": "approval", "to": "deliver", "when": "approved"}
  ],
  "entrypoint": "retrieve",
  "limits": {"maxTransitions": 12}
}
```

**Lưu ý:** `agent:quotation-drafter@2` là Agent chuyên trách được publish riêng. Compiler phải cấm self-recursive invocation không được khai báo; nếu một Agent tự gọi chính mình thành vòng lặp không bounded thì publish phải reject.

### 16.4 RunEvent contract

```json
{
  "schemaVersion": "1.0",
  "eventId": "evt_01H...",
  "runId": "run_01H...",
  "workspaceId": "ws_01H...",
  "sequence": 14,
  "type": "run.approval_requested",
  "timestamp": "2026-10-30T00:00:00Z",
  "traceId": "trace_...",
  "data": {"approvalRequestId": "apr_01H...", "stepId": "approval"}
}
```

Consumers phải bỏ qua event type không biết nếu compatibility policy cho phép; các event có side effects từ consumer phải idempotent. `sequence` monotonic trong một `runId` (không nhất thiết global). Redact secrets và PII trong payload; không truyền full prompts vào SSE mặc định.

## 17. Database: catalog tối thiểu và constraints

### 17.1 Schema `control` (do Control API quản lý)

| Table | Keys/constraints quan trọng | Notes |
|---|---|---|
| `workspaces` | PK `id`, unique `slug` | Tenant root |
| `users` | PK `id`, unique (`issuer`, `subject`) | OIDC identity |
| `memberships` | unique (`workspace_id`,`user_id`) | Roles per workspace |
| `agents` | unique (`workspace_id`,`slug`) | Stable logical identity |
| `agent_versions` | unique (`agent_id`,`version`), `spec_hash` | Immutable after publish |
| `agent_publications` | FK current version | Atomic pointer update |
| `business_processes` | workspace scope | Goal/examples/constraints |
| `workflows` | workspace scope | Logical workflow |
| `workflow_versions` | unique (`workflow_id`,`version`) | Immutable DSL |
| `tool_definitions` | unique (`workspace_id`,`tool_key`,`version`) | Schema/risk metadata |
| `tool_grants` | principal/workspace/tool-scope tuples | Allowed capability |
| `knowledge_bases` | workspace scope | Metadata/ACL |
| `documents` | FK knowledge base | Stable document identity |
| `document_versions` | FK document, content checksum, status | Draft/validated/published |
| `approvals` | PK, `action_hash`, `version`, `status` | CAS, expiry, approver |
| `audit_events` | append-only event ID | Timestamp, actor, resource, outcome |
| `outbox_events` | unique event ID, delivery state | Reliable event dispatch |

### 17.2 Schema `runtime` (do Runtime quản lý)

| Table | Keys/constraints quan trọng | Notes |
|---|---|---|
| `runs` | PK run ID, workspace, published bundle version | status, `trace_id`, timestamps |
| `run_steps` | FK run ID, logical step ID, attempt | durations, step status |
| `run_events` | unique (`run_id`, `sequence`) | SSE replay/read-model |
| `tool_receipts` | unique (`workspace_id`,`operation_key`) | Prevent duplicates on retry |
| `checkpoint_refs` | run/thread/checkpoint identifiers | Link to LangGraph persistence |
| `messages` | workspace, owner, conversation ID | Encrypt/retention |
| `memory_records` | workspace, owner, consent, TTL | Long-term data isolation |
| `runtime_outbox` | unique event ID | Event delivery |

Tất cả các truy vấn tenant-scoped phải enforce tenant in application queries; trong production có thể bật PostgreSQL Row Level Security làm defense-in-depth. Không lưu refresh token/API key plaintext trong DB; lưu `secret_ref` được resolve từ secret manager ở gateway.

### 17.3 Ví dụ transaction `publish`

```text
BEGIN
  SELECT agents FOR UPDATE (workspace_id, agent_id)
  CHECK latest_revision == expected_revision
  INSERT immutable agent_versions(spec, spec_hash, resolved_bundle_hash)
  UPDATE agent_publications SET agent_version_id = new_version
  INSERT audit_events(actor, action='publish', resource, outcome)
  INSERT outbox_events(type='agent.published', payload)
COMMIT
```

Đây là pseudocode; transaction implementation thực tế phải quản lý DB session, rollback, errors và tránh gọi network trong transaction.

## 18. REST APIs nên có vào từng mốc

| Endpoint | Owner | Khi nào implement | Test bắt buộc |
|---|---|---|---|
| `POST /v1/workspaces` | Control/workspaces | P0 | auth + uniqueness |
| `GET /v1/workspaces/{wid}/agents` | Control/agents | P0 | tenant filtering, pagination |
| `POST /v1/workspaces/{wid}/agents` | Control/agents | P0 | JSON contract |
| `PATCH /v1/workspaces/{wid}/agents/{id}/draft` | Control/agents | P0 | optimistic lock / ETag |
| `POST /v1/workspaces/{wid}/agents/{id}/publish` | Control/agents | P0 | check grants and immutable versions |
| `GET /v1/workspaces/{wid}/tools` | Control/tools | P0 | allowlisted visibility |
| `POST /v1/runs` | Runtime/runs | P0 | only pinned published version |
| `GET /v1/runs/{id}` | Runtime/runs | P0 | run ownership |
| `GET /v1/runs/{id}/events` | Runtime/events | P0 | SSE event replay/ACL |
| `POST /v1/runs/{id}/cancel` | Runtime/runs | P1 | idempotent cancel |
| `POST /v1/workspaces/{wid}/documents` | Control/knowledge | P1 | file type, size, ACL |
| `POST /v1/workspaces/{wid}/workflows/{id}/publish` | Control/workflows | P1 | cycle/loop bound test |
| `GET /v1/workspaces/{wid}/approvals` | Control/approvals | P2 | inbox scoped to approver |
| `POST /v1/workspaces/{wid}/approvals/{id}/decision` | Control/approvals | P2 | double approve/reject/expired |
| `POST /internal/v1/runs/{id}/resume` | Runtime/private | P2 | service auth + decision proof |
| `GET /v1/workspaces/{wid}/audit-events` | Control/audit | P2 | viewer permission + redaction |
| `GET /v1/workspaces/{wid}/eval-runs` | Control/operations | P3 | dataset/run versions |

Không tạo public `resume` nhận boolean, hoặc API upload code thực thi tùy ý. `internal/*` chỉ gọi bằng mTLS hoặc signed service credentials và có policy rõ ràng.

## 19. End-to-end sequence chi tiết: quotation approval (sandbox)

```text
1) Admin tạo draft AgentSpec -> Control validates JSON Schema
2) Admin chọn tool/knowledge đã được Registry phê duyệt
3) Admin Publish -> immutable agent_version + release hash
4) Operator Start Run -> Runtime validates signed principal and pinned version
5) Compiler expands graph nodes, checks references and bounds
6) Graph executes knowledge retrieval with tenant ACL filter
7) Agent drafts quotation WITHOUT external side effect
8) Workflow creates approval request with payload hash, expiry, approver role
9) Runtime commits checkpoint, run status becomes WAITING_APPROVAL
10) Approval Center displays exact action, recipient, diff, policy rule
11) Authorized approver decides; Control writes CAS record + outbox
12) Runtime verifies decision and resumes exact run/checkpoint
13) Tool Gateway rechecks policy/grant and stable operation key
14) Connector calls sandbox with idempotency key; stores receipt
15) Run events, audit, trace and usage become available in Operations UI
```

### 19.1 Bốn trạng thái phải được phân biệt

```text
Run: CREATED -> QUEUED -> RUNNING -> WAITING_APPROVAL -> RUNNING -> SUCCEEDED
                                                  └─ rejected -> CANCELLED
                                      RUNNING -> FAILED / TIMED_OUT
Approval: PENDING -> APPROVED / REJECTED / EXPIRED / CANCELLED
Tool operation: RESERVED -> IN_PROGRESS -> SUCCEEDED / FAILED / UNKNOWN
Agent config: DRAFT -> PUBLISHED -> ARCHIVED (history retained)
```

Phải định nghĩa State Transition Table + `expected_version` (optimistic concurrency) cho cả Run/Approval/ToolOperation. Trạng thái `UNKNOWN` dùng khi không biết một side effect có thành công hay không; giải quyết bằng lookup/reconciliation, không retry mù.

## 20. Security architecture và policies

### 20.1 Ba trust boundaries

1. **Browser → Control API:** session/JWT validation, workspace membership và per-operation RBAC; server có thể check ABAC hoặc policy.
2. **Control API → Runtime:** service identity và signed claims cho từng run; runtime không tin `workspaceId` client gửi như căn cứ cấp quyền.
3. **Runtime → External LLM/Tools/MCP:** egress allowlist, scoped credentials, schema/argument validation, policy + approval; tool outputs được xem như untrusted input.

### 20.2 Threat → nơi enforce

| Threat | File / nơi xử lý | Test |
|---|---|---|
| Prompt injection trong tài liệu | `safety/prompt_injection.py` + `tools/authorization.py` | malicious retrieved text không gọi được tool ngoài scope |
| Cross-tenant data leak | `workspaces/policy.py`, `knowledge/permissions.py` | user B không thấy tài liệu A |
| Overly permissive tool | `tools/grants.py`, `tools/authorization.py` | agent không có grant bị 403, không side effect |
| SSRF qua API/MCP Connector | `tools/mcp_client.py` + egress/network policy | chặn metadata IP, private CIDR, redirect tùy ý |
| Secret leakage | `observability/redaction.py` + secret manager | logs/traces/SSE không có secret |
| Replay approval | `approvals/service.py` + `tool_receipts` | same approval event không nhân đôi execution |
| Unbounded loops/token | `execution/limits.py` | vượt max steps/time/tokens dừng có kiểm soát |
| Knowledge poisoning | `knowledge/permissions.py`, source versioning | tài liệu chưa publish không được trích dẫn |

**Không coi text-based guardrail là thay thế security boundary.** OWASP Agentic Top 10 cần được dùng làm checklist threat modeling, nhưng hệ thống vẫn phải có thử nghiệm thực tế của từng control.

## 21. Observability: event và trace phải liên kết thế nào?

**Các correlation identifiers tối thiểu:** `workspace_id`, `agent_version_id`, `run_id`, `step_id`, `trace_id`, `tool_call_id`, `approval_id`, `idempotency_key` (hash/redacted ở log nếu cần).

```text
trace: run.execute
 ├─ spec.resolve
 ├─ graph.compile
 ├─ knowledge.retrieve
 ├─ llm.generate
 ├─ approval.request
 ├─ approval.wait             # elapsed wait is not LLM latency
 ├─ tool.authorize
 ├─ tool.execute
 └─ run.complete
```

Tách `active_processing_ms` khỏi `approval_wait_ms`. Không lấy thời gian chờ người duyệt để tính latency LLM. Thể hiện `tokens_source=provider_measured|estimated|unavailable`; không bịa con số tokens/cost khi provider không trả usage. Evaluation giữ `dataset_version`, `agent_version`, `model_config_hash` và `prompt_hash` để kết quả có thể tái lập.

## 22. Test matrix và definition of done

| Test code | Scope | Điều kiện PASS |
|---|---|---|
| `SPEC-01` | agent + workflow schemas | Invalid field, unknown node/tool bị reject từ server |
| `PUB-01` | versioning | Run luôn sử dụng exact pinned spec hash |
| `PUB-02` | concurrent edits | Stale draft update trả 409/412 |
| `SEC-01` | multi-tenancy | 100% cross-tenant fixtures không lộ dữ liệu hoặc tool grants |
| `SEC-02` | prompt injection | Malicious document không vượt tool allowlist |
| `SEC-03` | secret redaction | Không có API key/PII trong logs/trace sample |
| `STATE-01` | restart | Pending approval resume sau process restart |
| `APP-01` | approval race | Double approve chỉ có một CAS winner |
| `APP-02` | change payload | Approve payload hash cũ bị reject |
| `TOOL-01` | idempotency | Retry cùng operation key không tạo side effect trùng |
| `TOOL-02` | uncertain response | UNKNOWN outcome đi reconciliation, không blind retry |
| `RAG-01` | ACL | Unpublished/cross-tenant chunks không lọt retrieval |
| `MEM-01` | isolation | User A facts không lộ qua user B |
| `STREAM-01` | SSE reconnect | Resume từ last event ID không duplicate side effects |
| `OBS-01` | tracing | Run ID liên kết được trace, steps, approval, receipts |
| `EVAL-01` | regression | Golden set được chấm theo cùng baseline và phiên bản |

`Definition of Done` cho một module: API/contract có owner, migration có rollback plan, tests pass, deny unauthorized, telemetry không lộ secret, tài liệu README cập nhật, và đã có evidence của các tình huống failure liên quan. **Nếu chưa chạy, ghi NOT_RUN**; không sử dụng số giả lập làm production metric.

## 23. Delivery Plan theo PR nhỏ và file tạo thực tế

| Milestone / PR | Files cần tạo hoặc sửa | Demo nghiệm thu |
|---|---|---|
| `PR-00` Repo bootstrap | root configs, lockfiles, CI, `infra/compose`, `README`, `AGENTS` | `make doctor`, `make dev-up`, health endpoints |
| `PR-01` Contracts | `contracts/jsonschema/*`, codegen config, compatibility tests | Spec invalid bị reject ở TS và Python |
| `PR-02` Identity + workspace | `control_api/core/auth.py`, `modules/identity`, `modules/workspaces` | Không đọc workspace khác |
| `PR-03` Agent Registry | `modules/agents`, `modules/tools`, migrations | Tạo draft, publish version và xem version history |
| `PR-04` Runtime happy path | `application/run_service.py`, `compilation/*`, `engines/*`, events | One agent run trả kết quả thật hoặc lỗi có kiểm soát |
| `PR-05` Studio MVP | `features/agent-builder`, `features/agent-playground`, generated SDK | Tạo agent, chọn tools, publish, run trên UI |
| `PR-06` Knowledge | `modules/knowledge`, `runtime/knowledge`, object store, Qdrant | Upload, chunk, publish, retrieve citation + ACL |
| `PR-07` Workflow + HITL | `modules/workflows`, `modules/approvals`, runtime state/receipts | Pause/restart/approve/reject/resume đúng |
| `PR-08` Operations | run monitor, telemetry, usage, audit | Một run truy ra đầy đủ timeline |
| `PR-09` Eval/Security hardening | `evals/*`, `tests/security`, `tests/recovery` | Release gate pass hoặc block có lý do |

Thứ tự PR là **thứ tự dependency**, không phải lịch cam kết. Một nhóm nhỏ có thể triển khai PR song song nếu contracts và ownership đã chốt.

## 24. Checklist trước khi giao cho AI coding assistant

```text
[ ] Đọc README.md, AGENTS.md, AGENT_CODING_RULES.md, docs/architecture/*
[ ] Hiểu ranh giới Control/Runtime và ownership DB schemas
[ ] Xác định PR/milestone duy nhất được giao
[ ] Chỉ tạo file cần thiết, không tạo 100 file stub
[ ] Mọi public route có principal validation + tenant authorization
[ ] Contracts ưu tiên có schemaVersion và generated type tests
[ ] Runtime dùng pinned version, không resolve latest giữa run
[ ] Tool writes có authorization/approval/idempotency/audit
[ ] Không secret trong code, spec, logs hoặc prompt
[ ] Có tests và bằng chứng chạy (PASSED/FAILED/NOT_RUN)
[ ] Không tự thêm A2A/GraphRAG/Temporal/microservices khi chưa có yêu cầu
[ ] PR mô tả schema changes, migration plan, tests và threat impact
```

## 25. Nguồn lý thuyết/implementation tham chiếu cho từng quyết định

- **FPT AI Agents**: Persona, Task Agents, Business Processes, Knowledge & chunk editing, Tools, Guardrails, versioning và audit — https://docs.fpt.ai/docs/vi/aiagents/documentation/aiagent/
- **FPT AI EOS**: digital worker workflows, phối hợp con người/AI, kiểm soát hoạt động — https://fpt.ai/vi/tin-tuc/trinh-lang-fpt-ai-eos-fpt-ai-mo-ra-cach-thuc-van-hanh-moi-voi-nhan-su-so/
- **Agno Studio + Registry**: mã khai báo tool/model/schema rồi Studio cho người dùng chọn — https://docs.agno.com/agent-os/studio/registry
- **Agno Components Versioning**: draft/version/publish/restore và compare-and-set — https://docs.agno.com/examples/agent-os/studio/registry-and-components
- **LangGraph Persistence & Interrupts**: thread checkpoint, long-term store, HITL resume — https://docs.langchain.com/oss/python/langgraph/persistence và https://docs.langchain.com/oss/python/langgraph/interrupts
- **FastAPI bigger applications**: modular API router và dependencies — https://fastapi.tiangolo.com/tutorial/bigger-applications/
- **Next.js App Router**: route groups, project organization — https://nextjs.org/docs/app/getting-started/project-structure
- **MCP 2025-11-25**: stdio và Streamable HTTP, transport/security — https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- **OWASP Top 10 Agentic Applications 2026**: security risk taxonomy — https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- **VinAI Index của người dùng**: VIN-P1-D03/D08/D09A/D11/D13/D14, VIN-T2-REF, VIN-T3-D08/D17/D24/D25/D26A, VIN-COMP-ARENA để biến thành module-specific learning/test tasks. Các benchmark trong index chưa được mình tái lập.

## 26. Những việc chủ động chưa làm trong MVP

- Không viết tự động code Python/JS từ UI rồi chạy trong process Agent Runtime.
- Không xây chợ Agent công khai, thanh toán, multi-region Kubernetes, marketplace billing.
- Không thêm A2A, Kafka, Delta Lake, full GraphRAG, Zep, LoRA/DPO chỉ để có đủ stack.
- Không dùng Temporal song song với LangGraph khi chưa chứng minh yêu cầu durable workflows vượt giải pháp hiện tại.
- Không tự tạo một tính năng 'AI OS Manager tự sửa production Agent' khi chưa có policy, approval, governance và sandbox.
- Không nhận định đạt 99.9% SLO/đạt 100% accuracy nếu chưa có hệ thống đo và mẫu kiểm thử thực tế.

**V1 kết luận:** Thư mục chỉ là bề mặt. Để một repository thực sự production-ready, cần đồng thời có domain ownership, immutable contracts, durable state, authorization, idempotency, observability, automated tests và tài liệu kiến trúc. Bắt đầu từ một vertical slice có thể kiểm chứng, không dựng toàn bộ cây thư mục rồi bỏ trống.
