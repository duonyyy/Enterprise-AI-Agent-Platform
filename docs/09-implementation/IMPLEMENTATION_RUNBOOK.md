# ENTERPRISE AI AGENT PLATFORM — IMPLEMENTATION RUNBOOK V1.0 (56 TABLES)

> **Loại tài liệu:** Hướng dẫn thực hành theo từng bước (execution runbook, có kiểm chứng).  
> **Cập nhật:** 2026-10-30.  
> **Kiến trúc cơ sở dữ liệu:** Baseline V1.0 Greenfield Unified (56 bảng, 7 schemas, 55 RLS policies, 10 immutability triggers).  
> **Tích hợp mở rộng:** Hợp nhất phân hệ **Agent Skill Management** (on-demand token containment) và **Model Context Protocol (MCP) Gateway** (SSRF-safe, 12-step execution pipeline).  
> **Dự án:** Enterprise AI Agent Platform **độc lập**, tham khảo FPT AI Agents + FPT AI EOS; **không liên quan RetailMind-AI**.  
> **Mục tiêu mốc đầu tiên:** `Create Agent → Attach Skills & MCP Tools → Publish immutable version → Start Run → 12-Step ToolDispatcher → Human Approval → Restart → Resume → Finish + Audit`.  
> **Trạng thái ban đầu:** Kiến trúc và SQL **DESIGN / VERIFIED STATIC PASS**. Không nhầm ví dụ/lệnh trong tài liệu với kết quả đã chạy trên database production thực tế.

## 0. Hướng dẫn đọc và thực hiện

Đây là tài liệu thao tác theo thứ tự; **mỗi bước có mục tiêu, đầu vào, việc làm, lệnh/file, bằng chứng, PASS/FAIL và cách phục hồi**. Các khối lệnh là ví dụ có thể điều chỉnh cho máy của bạn; không chạy trên database có dữ liệu thực. Đường dẫn tính từ **gốc repository** `enterprise-agent-platform/`, trừ khi ghi rõ khác. Các file SQL đã có ở gói thiết kế V1 **phải được copy vào vị trí tương ứng**; runbook quản lý thống nhất toàn bộ 56 bảng theo kiến trúc Greenfield Unified.

### 0.1. Bộ tài liệu đầu vào cần giữ nguyên

| Tài liệu/file từ giai đoạn thiết kế V1 | Dùng cho bước | Trạng thái |
|---|---|---|
| `docs/02-architecture/ENTERPRISE_AGENT_PLATFORM_REPOSITORY_IMPLEMENTATION_V1.md` | Cấu trúc source, ranh giới module | Blueprint V1.0 |
| `docs/03-database/database/ENTERPRISE_AGENT_PLATFORM_DATABASE_DESIGN_V1.md` | ERD 56 bảng, ownership, transaction, retention, ADR-013 | Blueprint V1.0 |
| `docs/03-database/database/ENTERPRISE_AGENT_PLATFORM_DATA_DICTIONARY_V1.md` | 56 bảng / 7 schemas, kiểu cột và constraints | Blueprint V1.0 |
| `database/migrations/0001_baseline.sql` | Khởi tạo DB mới thống nhất 56 bảng, 55 RLS, 10 triggers | Đã pass static validation |
| `database/tests/0002_smoke.sql` | Smoke test trên DB thử nghiệm 56 bảng | Đã chuẩn bị |
| `docs/05-change-requests/CR-SKILL-001.md` | Đặc tả catalog skill, on-demand loading, zip-slip defense | Blueprint V1.0 |
| `docs/05-change-requests/CR-MCP-001.md` | Đặc tả kết nối MCP, SSRF defense, tool mapping | Blueprint V1.0 |

**Không** lấy kết quả `static validation` làm kết quả chạy PostgreSQL. Bất cứ lỗi DDL nào phải được sửa trong một patch được review rồi chạy lại trên DB mới; không dùng mẹo bỏ qua lỗi hoặc tắt RLS cho qua test.

### 0.2. Hai mức hoàn thành

- **Milestone A — Backend Foundation:** Database mới 56 bảng migrate được, 55 chính sách RLS test đạt, 10 immutability triggers bảo vệ phiên bản, API xác thực và phân quyền, Agent Draft/Publish chuẩn hóa kèm Skill Refs và MCP Tool Mappings, tests/CI đạt.
- **Milestone B — Agent Vertical Slice:** Runtime thực thi bản published, nạp Skill on-demand (tiết kiệm token), gọi MCP tool qua 12-Step ToolDispatcher có SSRF shield, workflow checkpoint, phê duyệt con người (HITL), tool idempotency, resume sau restart và audit/trace hoàn chỉnh.

**Chưa nằm trong mốc đầu:** visual workflow kéo thả hoàn chỉnh, Marketplace bên thứ ba, A2A đa tổ chức, multi-region, fine-tuning, GraphRAG, GPU cluster, billing thương mại. Không tách microservice thêm khi chưa có lý do đo được.

### 0.3. Quy ước trạng thái và bằng chứng

Tất cả checklist trong tài liệu khởi đầu ở `[ ]`. Chỉ đánh `[x]` nếu đã lưu bằng chứng của **môi trường hiện tại**:

```text
artifacts/evidence/<PR-ID>/<run-date>/
├── commands.log           # Lệnh chạy, đã xóa mật khẩu/token
├── test-results.xml       # Pytest/CI
├── schema-check.txt       # pg_catalog checks (56 bảng, 55 RLS, 10 triggers)
├── authz-results.txt      # 401/403/404 và RLS test
├── traces.json            # Trace đã loại PII
└── summary.md             # SHA commit, versions, PASS/FAIL, blockers
```

Định nghĩa `PASS`: command exit code 0, assertion đúng (đủ 56 bảng, 55 RLS, 10 triggers), không bỏ qua test có ảnh hưởng đến security, có output/trace chứng minh. `BLOCKED`: phụ thuộc chưa hoàn tất. `FAIL`: assertion sai hoặc migration/code có lỗi. Không tự suy đoán benchmark latency hoặc số lượng token.

---

## 1. Bản đồ công việc và dependency

| ID | Giai đoạn | Đầu ra bắt buộc | Gate |
|---|---|---|---|
| `PR-00` | Repo + môi trường | Monorepo chạy `docker compose config` và health check | G0 |
| `DB-01` | PostgreSQL + baseline V1 | **56 bảng**, 7 schemas, 55 RLS, 10 triggers được DB chấp nhận | G1 |
| `DB-02` | Roles + RLS thực sự | Tenant isolation với non-bypass role; denied cross-tenant DML | G2 |
| `DB-03` | Alembic + backup drill | Baseline 56 bảng versioned, restore thành công trên DB khác | G3 |
| `API-01` | Contracts + FastAPI | OpenAPI, health, error envelope, Skill & MCP Schemas | G4 |
| `API-02` | Authn/Authz + Tenant Context | JWT verify, membership, permission matrix, RLS context injection | G5 |
| `API-03` | Agent, Skill & MCP Registry | Draft, optimistic revision, publish immutable, frozen spec, deployment pointer | G6 |
| `RT-01` | Runtime Foundation | Start run pinned to published spec, event log, trace, Skill on-demand loader | G7 |
| `RT-02` | Tool Gateway & MCP Adapter | 12-step dispatcher, tool allowlist, SSRF protection, idempotency | G8 |
| `RT-03` | Workflow + HITL Recovery | Interrupt, approval transaction, restart/resume, checkpoint bền vững | G9 |
| `OPS-01` | Tests + Observability | Security, recovery, evaluation, Zip-Slip & SSRF test suite | G10 |
| `WEB-01` | Minimal Studio & Console | Tạo/publish/approve, quản lý Skill/MCP và theo dõi run từ UI | G11 |
| `RELEASE-01` | Pilot release | Restore drill, rollback, runbook on-call, release decision | G12 |

**Luật phụ thuộc:** `DB-01→DB-02→DB-03`, rồi `API-01→API-02→API-03→RT-01→RT-02→RT-03`. `OPS-01` thực hiện song song từ đầu nhưng Gate G10 phải đạt trước pilot. `WEB-01` có thể phác thảo sớm, nhưng không dùng giao diện để che lỗi API/runtime.

---

## 2. PR-00 — Khởi tạo repository và công cụ

### 2.1. Prerequisites

- Windows 11 + PowerShell 7 **hoặc** Linux/macOS shell; Docker Desktop (WSL2 backend nếu trên Windows), Git, Python 3.11+, `uv`, Node.js LTS, pnpm. Chỉ cài công cụ bạn thực sự dùng ở mốc hiện tại.
- PostgreSQL **16** trong Docker để kiểm thử với baseline `>=16`; ghi digest image/lock dependencies sau khi PoC pass.
- Không commit `.env`, credentials, tokens, dữ liệu cá nhân.

**Kiểm tra:**

```bash
git --version
docker --version
docker compose version
python --version
uv --version
node --version
pnpm --version
```

Nếu một lệnh chưa cài, ghi `BLOCKED`, cài trước khi làm tiếp. Không cần Qdrant/Redis ở bước này.

### 2.2. Tạo repo / cấu trúc tối thiểu tích hợp Skill & MCP

```text
enterprise-agent-platform/
├── apps/
│   ├── control-api/
│   │   └── src/control_api/modules/
│   │       ├── identity/
│   │       ├── workspaces/
│   │       ├── agents/
│   │       ├── skills/               # Quản trị Skill Catalog, versions, manifest
│   │       ├── mcp/                  # Quản trị MCP Connections & Discovery
│   │       ├── tools/
│   │       └── approvals/
│   ├── agent-runtime/
│   │   └── src/agent_runtime/
│   │       ├── skills/               # CatalogClient, Loader, ResourceReader (anti-ZipSlip)
│   │       ├── mcp/                  # McpClientAdapter, SSRF Defense, Error taxonomy
│   │       ├── tools/                # 12-Step ToolDispatcher, Idempotency
│   │       └── engines/              # LangGraph checkpointer & resume
│   └── studio-web/
│       └── src/
│           ├── app/(console)/w/[workspaceId]/
│           │   ├── skills/           # UI Quản lý Skill
│           │   └── mcp/              # UI Quản lý MCP Server connections
│           └── features/
│               ├── skill-management/
│               └── mcp-management/
├── contracts/
│   ├── jsonschema/
│   │   ├── agent-spec.v1.schema.json
│   │   ├── skill-manifest.v1.schema.json
│   │   ├── mcp-connection.v1.schema.json
│   │   ├── mcp-discovery.v1.schema.json
│   │   ├── tool-spec.v1.schema.json
│   │   ├── workflow-spec.v1.schema.json
│   │   ├── approval-decision.v1.schema.json
│   │   └── run-event.v1.schema.json
│   ├── openapi/
│   └── events/
├── database/
│   ├── migrations/
│   │   └── 0001_baseline.sql        # 56 bảng V1.0
│   ├── tests/
│   │   └── 0002_smoke.sql           # Smoke test 56 bảng
│   └── seeds/
├── infra/
│   ├── compose.yaml
│   └── postgres/
│       └── init.sql                 # Baseline V1.0
├── examples/
│   ├── skills/weekly-report/        # Mẫu Skill chuẩn SKILL.md
│   └── mcp/                         # Mẫu MCP config
├── docs/
├── tests/
│   ├── contract/
│   ├── security/
│   ├── e2e/
│   └── recovery/
├── artifacts/evidence/
├── .env.example
├── .gitignore
└── README.md
```

**PowerShell copy bộ file SQL V1.0:**

```powershell
Copy-Item "database/migrations/0001_baseline.sql" `
  "database/migrations/0001_baseline.sql"
Copy-Item "database/tests/0002_smoke.sql" `
  "database/tests/0002_smoke.sql"
```

**Bằng chứng:** `git status`, `Get-FileHash database/migrations/0001_baseline.sql -Algorithm SHA256`, ghi hash vào `artifacts/evidence/PR-00/.../summary.md`. Không tự đổi nội dung baseline không có ADR/review.

### 2.3. `.env.example` (không đưa password thật)

```dotenv
POSTGRES_DB=agent_platform
POSTGRES_USER=eap_admin
POSTGRES_PASSWORD=REPLACE_WITH_LOCAL_SECRET
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5432
APP_ENV=local
```

Tạo `.env` từ `.env.example` rồi **thay** `POSTGRES_PASSWORD` bằng mật khẩu mạnh, chỉ dùng local.

### 2.4. `infra/compose.yaml` (PostgreSQL local-only)

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 20
volumes:
  postgres_data:
```

**Lệnh chạy từ root:**

```bash
docker compose --env-file .env -f infra/compose.yaml config
docker compose --env-file .env -f infra/compose.yaml up -d db
docker compose --env-file .env -f infra/compose.yaml ps
docker compose --env-file .env -f infra/compose.yaml exec -T db   pg_isready -U eap_admin -d agent_platform
```

**PASS G0:** `docker compose config` hợp lệ; container healthy; `pg_isready` trả `accepting connections`; không có port lộ ra `0.0.0.0`.

---

## 3. DB-01 — Chạy baseline V1.0 trên PostgreSQL thật (56 bảng)

### 3.1. Preflight (DB trống, disposable)

**Không chạy baseline vào database đã tồn tại bảng của ứng dụng.** Kiểm tra:

```bash
docker compose --env-file .env -f infra/compose.yaml exec -T db   psql -U eap_admin -d agent_platform -v ON_ERROR_STOP=1   -c "SELECT current_database(), current_user, version();"
```

```sql
SELECT count(*) AS app_tables
FROM information_schema.tables
WHERE table_type='BASE TABLE'
  AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops');
-- Mong đợi: 0 trước khi áp baseline
```

### 3.2. Chạy một lần

```bash
docker compose --env-file .env -f infra/compose.yaml cp   database/migrations/0001_baseline.sql db:/tmp/0001_baseline.sql

docker compose --env-file .env -f infra/compose.yaml exec -T db   psql -U eap_admin -d agent_platform -v ON_ERROR_STOP=1   -f /tmp/0001_baseline.sql
```

**PowerShell (không nối dòng):**

```powershell
docker compose --env-file .env -f infra/compose.yaml cp database/migrations/0001_baseline.sql db:/tmp/0001_baseline.sql
docker compose --env-file .env -f infra/compose.yaml exec -T db psql -U eap_admin -d agent_platform -v ON_ERROR_STOP=1 -f /tmp/0001_baseline.sql
```

File baseline gồm `BEGIN/COMMIT`, nên nếu lỗi DDL trong transaction, **không chọn chạy tiếp những statement còn lại**. Lưu stderr, sửa migration qua PR, xóa DB test và chạy lại từ trạng thái sạch. Khởi tạo một lần đồng nhất 56 bảng (Greenfield Unified).

### 3.3. Postflight / SQL assertions cho 56 bảng

Chạy từng truy vấn bằng `psql -c` hoặc mở psql tương tác:

```sql
-- 1. Tổng số bảng trong baseline V1:
SELECT count(*) AS tables_count
FROM information_schema.tables
WHERE table_type='BASE TABLE'
  AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops');
-- EXPECTED: 56

-- 2. Phân bố chi tiết theo từng schema:
SELECT table_schema, count(*)
FROM information_schema.tables
WHERE table_type='BASE TABLE'
  AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
GROUP BY table_schema ORDER BY table_schema;
-- EXPECTED:
-- iam: 5
-- catalog: 16       (+3 bảng Skill: skills, skill_versions, agent_skills)
-- knowledge: 7
-- runtime: 12
-- integration: 6   (+3 bảng MCP: mcp_connections, mcp_connection_secrets, mcp_tool_mappings)
-- governance: 4
-- ops: 6
-- TỔNG CỘNG: 56

-- 3. Kiểm tra kiểm soát RLS qua pg_class:
SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind='r' AND n.nspname IN
  ('iam','catalog','knowledge','runtime','integration','governance','ops')
ORDER BY n.nspname,c.relname;
-- EXPECTED: 55 bảng tenant-scoped có relrowsecurity=true, relforcerowsecurity=true.
-- LƯU Ý: iam.users là bảng global identity duy nhất có relrowsecurity=false.

-- 4. Kiểm tra sự tồn tại của 10 immutability triggers bảo vệ snapshot:
SELECT tgrelid::regclass AS table_name, tgname AS trigger_name
FROM pg_trigger
WHERE tgname LIKE 'tg_prevent_immutability_%'
ORDER BY table_name;
-- EXPECTED: 10 triggers:
-- 1. catalog.agent_versions
-- 2. catalog.skill_versions
-- 3. catalog.tool_versions
-- 4. catalog.workflow_versions
-- 5. catalog.business_process_versions
-- 6. knowledge.dataset_versions
-- 7. runtime.run_steps
-- 8. runtime.run_events
-- 9. runtime.checkpoint_refs
-- 10. governance.audit_events
```

**PASS G1:** **56 bảng** tồn tại đầy đủ; 7 schemas đúng số lượng (`iam=5, catalog=16, knowledge=7, runtime=12, integration=6, governance=4, ops=6`); các `CREATE TABLE/FK/INDEX/TRIGGER` trong baseline chạy thành công; **55 bảng tenant-scoped** có `relrowsecurity=true`, `relforcerowsecurity=true`, RLS policies tồn tại; **10 immutability triggers** gắn đúng bảng; `iam.users` là identity table global phải được hạn chế bằng GRANT/API riêng.

### 3.4. Khi DDL không chạy

- Lưu đúng statement lỗi, PostgreSQL error code và version server.
- Kiểm tra thứ tự FK, thiếu unique target, kiểm tra `CREATE FUNCTION`/trigger và default/check conditions.
- Chạy script kiểm tra tĩnh `python docs/03-database/database/validate_db_static.py` trước khi nạp vào Postgres.

---

## 4. DB-02 — Database roles, GRANT và RLS thực sự trên 56 bảng

### 4.1. Phân biệt các loại tài khoản và quyền hạn

| Tài khoản/role | Nhiệm vụ | Quy tắc an toàn |
|---|---|---|
| `eap_admin` | Bootstrap/migration DB local | Không sử dụng trong app runtime |
| `eap_control` | Control API | Không `BYPASSRLS`, chỉ quyền cần dùng (quản trị Catalog, Skill, MCP config) |
| `eap_runtime` | Agent Runtime / worker | Không `BYPASSRLS`, chỉ quyền cần dùng (chỉ đọc Published Spec, nạp Skill, ghi Runs, tuyệt đối cấm đọc secrets) |
| `eap_smoke` | Chạy smoke trên **DB test disposable** | Được cấp DML rộng phục vụ kiểm thử, **cấm production** |

```sql
CREATE ROLE eap_control LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE eap_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE eap_smoke LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT CONNECT ON DATABASE agent_platform TO eap_control, eap_runtime, eap_smoke;
GRANT USAGE ON SCHEMA iam,catalog,knowledge,runtime,integration,governance,ops
TO eap_control,eap_runtime,eap_smoke;

-- CHỈ trên database thử nghiệm, phục vụ smoke và không đưa vào production:
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA
  iam,catalog,knowledge,runtime,integration,governance,ops TO eap_smoke;
```

**Cấu hình phân quyền chi tiết cho Control API và Runtime (bao gồm Skill & MCP):**

```sql
-- Control API:
GRANT SELECT, INSERT, UPDATE ON iam.workspaces, iam.memberships,
  catalog.agents, catalog.agent_publications,
  catalog.skills, catalog.agent_skills,
  integration.mcp_connections, integration.mcp_connection_secrets, integration.mcp_tool_mappings
  TO eap_control;

GRANT SELECT, INSERT ON catalog.agent_versions, catalog.skill_versions TO eap_control;

-- Agent Runtime:
GRANT SELECT ON catalog.agents, catalog.agent_versions,
  catalog.agent_publications, catalog.tool_versions,
  catalog.workflow_versions,
  catalog.skills, catalog.skill_versions, catalog.agent_skills,
  integration.mcp_connections, integration.mcp_tool_mappings
  TO eap_runtime;

-- TUYỆT ĐỐI KHÔNG CẤP QUYỀN ĐỌC integration.mcp_connection_secrets CHO eap_runtime.
-- Secrets chỉ được giải mã server-side tại Tool Gateway với audit trail nghiêm ngặt.

GRANT SELECT, INSERT, UPDATE ON runtime.runs, runtime.run_steps,
  runtime.run_events, runtime.approval_requests, runtime.tool_operations,
  runtime.jobs, runtime.checkpoint_refs TO eap_runtime;
```

### 4.2. Chạy smoke với non-bypass role

```bash
docker compose --env-file .env -f infra/compose.yaml cp   database/tests/0002_smoke.sql db:/tmp/0002_smoke.sql

docker compose --env-file .env -f infra/compose.yaml exec -T db   psql -U eap_smoke -d agent_platform -v ON_ERROR_STOP=1   -f /tmp/0002_smoke.sql
```

`0002_smoke.sql` tự động kiểm thử:
1. Đặt context Tenant A -> kiểm tra chỉ thấy dữ liệu Tenant A.
2. Kiểm tra không thấy tài nguyên của Tenant B.
3. Thử `UPDATE catalog.agent_versions` hoặc `catalog.skill_versions` -> bắt buộc ném ngoại lệ do Immutability Trigger chặn.
4. Tự `ROLLBACK` an toàn.

### 4.3. Bổ sung `database/tests/0003_rls_negative.sql`

1. **No tenant context:** không được thấy dữ liệu tenant-scoped trên bất kỳ bảng nào trong 55 bảng.
2. **Tenant A SELECT/UPDATE/DELETE Tenant B:** bị chặn hoàn toàn bởi RLS.
3. **Cross-workspace:** cùng tenant nhưng workspace khác phải bị API authorization từ chối.
4. **Immutability checks:** UPDATE/DELETE trên 10 bảng snapshot đều thất bại.

### 4.4. PASS G2

- [ ] `eap_control`, `eap_runtime`: `rolbypassrls=false`, không superuser, không owner.
- [ ] Smoke file 56 bảng chạy thành công bằng `eap_smoke`, không phải `eap_admin`.
- [ ] Negative tests cho `SELECT/INSERT/UPDATE/DELETE` có bằng chứng.
- [ ] 10 immutability triggers được xác nhận chặn đứng mọi hành vi sửa snapshot.

---

## 5. DB-03 — Alembic, migration safety, backup/restore (56 bảng)

### 5.1. Lựa chọn nguồn migration duy nhất

**Quy tắc:** Giữ `0001_baseline.sql` làm baseline V1.0 hoàn chỉnh 56 bảng, nạp lên **DB mới** bằng `psql`; dùng **Alembic cho mọi migration tiếp theo (từ 0002 trở đi)**. Không bao giờ tạo Alembic revision tạo trùng 56 bảng ban đầu.

```bash
cd apps/control-api
uv add fastapi uvicorn sqlalchemy alembic 'psycopg[binary]' pydantic-settings
uv run alembic init migrations
uv run alembic revision -m "baseline_marker_v1_56_tables"
# Cấu hình target_metadata và env.py, sau đó stamp DB:
uv run alembic stamp head
uv run alembic current
```

### 5.2. Backup và restore drill trên local database

```bash
docker compose --env-file .env -f infra/compose.yaml exec -T db   pg_dump -U eap_admin -d agent_platform -Fc -f /tmp/agent_platform.dump

docker compose --env-file .env -f infra/compose.yaml exec -T db   createdb -U eap_admin agent_platform_restore

docker compose --env-file .env -f infra/compose.yaml exec -T db   pg_restore -U eap_admin -d agent_platform_restore   --no-owner --no-acl /tmp/agent_platform.dump
```

Kiểm tra DB `agent_platform_restore` có đủ 56 bảng, 55 RLS policies, 10 triggers.

**PASS G3:** Alembic `current` hợp lệ; restore qua DB mới thành công đủ 56 bảng.

---

## 6. API-01 — Thiết kế hợp đồng API, Schemas và FastAPI Foundation

### 6.1. Bộ JSON Schemas hợp đồng (`contracts/jsonschema/`)

Mọi thực thể phải có JSON Schema chuẩn mực trước khi code nghiệp vụ:
1. `agent-spec.v1.schema.json`: Có trường `skillRefs: [{"skillId": "...", "version": 1}]` và `toolRefs`.
2. `skill-manifest.v1.schema.json`: Chuẩn hóa manifest SKILL.md (metadata, parameters, permissions, files).
3. `mcp-connection.v1.schema.json`: Chuẩn hóa cấu hình kết nối MCP (`transport`, `command`, `url`, `headers`, `security`).
4. `mcp-discovery.v1.schema.json`: Snapshot công cụ / tài nguyên phát hiện từ MCP server.
5. `tool-spec.v1.schema.json`: Hỗ trợ kind `builtin`, `http`, và `mcp` (`mcpConfig`).
6. `workflow-spec.v1.schema.json`, `approval-decision.v1.schema.json`, `run-event.v1.schema.json`.

### 6.2. Danh mục API Endpoints cốt lõi

| Phân hệ | Endpoint | Quyền | Ghi chú |
|---|---|---|---|
| Health | `GET /health/live`, `GET /health/ready` | Public/Internal | Kiểm tra process & DB connection |
| Agents | `POST /api/v1/workspaces/{ws}/agents` | Developer | Tạo draft spec |
| Agents | `POST /api/v1/workspaces/{ws}/agents/{id}/publish` | Admin/Dev | Freeze spec, tính bundle_sha256, ghi version |
| Skills | `POST /api/v1/workspaces/{ws}/skills` | Developer | Đăng ký Skill mới |
| Skills | `POST /api/v1/workspaces/{ws}/skills/import` | Developer | Import từ SKILL.md hoặc folder zip |
| Skills | `POST /api/v1/workspaces/{ws}/skills/{id}/publish` | Admin/Dev | Đóng băng skill_version bất biến |
| MCP | `POST /api/v1/workspaces/{ws}/mcp/connections` | Developer | Khởi tạo MCP connection |
| MCP | `POST /api/v1/workspaces/{ws}/mcp/connections/{id}/discover` | Developer | Phát hiện tools/resources từ MCP server |
| MCP | `POST /api/v1/workspaces/{ws}/mcp/connections/{id}/sync-tools` | Developer | Đồng bộ tools vào catalog |
| Runs | `POST /api/v1/workspaces/{ws}/agents/{id}/runs` | Operator | Kích hoạt run (idempotency key) |
| Approvals | `POST /api/v1/workspaces/{ws}/approvals/{id}/decisions` | Approver | Phê duyệt/từ chối hành động side-effect |

**PASS G4:** OpenAPI sinh tự động khớp với các schemas; health check ping DB thật; schema validation từ chối các trường lạ (`additionalProperties: false`).

---

## 7. API-02 — Authentication + Authorization + Transaction Context

### 7.1. Định danh và phân quyền

- Xác thực bằng OIDC/JWT từ IdP, kiểm tra chữ ký qua JWKS, `iss`, `aud`, `exp`.
- Tra `iam.users` theo `(issuer, subject)`; nạp quyền dựa trên `iam.memberships` trong workspace.
- Nghiêm cấm nhận `X-Tenant-ID` hay `workspace_id` tùy ý từ client header; tenant context bắt buộc suy diễn từ phiên xác thực máy chủ.

### 7.2. Transaction RLS Context Injection

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def set_verified_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    # tenant_id lấy từ token đã verify và membership check
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
```

**PASS G5:** Toàn bộ test SEC-01 đến SEC-09 đạt; không rò rỉ tenant context qua connection pool; log không in token/secret.

---

## 8. API-03 — Agent, Skill & MCP Registry: Draft, Versioning và Publish

### 8.1. Nghiệp vụ Quản trị Skill
- **Import/Parse:** Phân tích cú pháp `SKILL.md`, trích xuất YAML frontmatter (`name`, `description`, `parameters`, `allowed_tools`), kiểm tra schema qua `skill-manifest.v1.schema.json`.
- **Packaging:** Đóng gói thư mục skill và tài liệu hướng dẫn tham chiếu (`references/`).
- **Publish Skill:** Tính toán sha256 checksum của toàn bộ bundle, ghi vào `catalog.skill_versions` với `frozen_spec` bất biến.

### 8.2. Nghiệp vụ Quản trị MCP Connection
- **Cấu hình Transport:** Hỗ trợ `stdio` (chạy command trong container an toàn) và `sse` / `streamable_http`.
- **Bảo mật Secret:** Khóa API, token được mã hóa và lưu trong `integration.mcp_connection_secrets`, spec công khai chỉ chứa `secret_ref`.
- **SSRF Validation:** Trước khi kích hoạt kết nối `sse`/`http`, kiểm tra URL target chống lại danh sách IP cấm (Loopback, Private IP, Cloud Metadata).

### 8.3. Nghiệp vụ Publish Agent hoàn chỉnh
1. Lock row `catalog.agents` trong transaction; kiểm tra optimistic revision.
2. Freeze toàn bộ dependencies: `model_config`, `tool_versions`, `workflow_version`, và **`skill_versions`**.
3. Tính toán `bundle_sha256` bao gồm toàn bộ hash của các dependencies cấu thành.
4. INSERT `catalog.agent_versions` với version mới và `frozen_spec`.
5. Cập nhật con trỏ môi trường trong `catalog.agent_publications`.
6. Ghi đồng thời `governance.audit_events` và `integration.outbox_events` trong cùng transaction.

**PASS G6:** Agent versions và Skill versions được đóng băng bất biến; race condition được kiểm soát bằng lock và CAS revision; attempt sửa snapshot bị trigger từ chối.

---

## 9. RT-01 — Agent Runtime Foundation & Skill Loading

### 9.1. Nạp Skill On-Demand & Kiểm soát Context Token

Khác với Tool truyền thống luôn nhồi toàn bộ schema vào system prompt:
1. **Metadata Discovery:** Khi khởi chạy Agent, Runtime chỉ tải thông tin tóm tắt (`name`, `description`) của các Skills đính kèm thông qua `SkillCatalogClient`.
2. **On-Demand Loading:** Khi LLM quyết định kích hoạt một Skill cụ thể, `SkillLoader` mới tải nội dung hướng dẫn chi tiết từ `SKILL.md` vào ngữ cảnh phiên làm việc.
3. **Token Containment:** Giải phóng bộ nhớ ngữ cảnh ngay khi tác vụ của Skill hoàn thành, tránh làm phình to context window và tiết kiệm chi phí suy luận.

### 9.2. State Machine và Job Leasing
- Runtime tuân thủ nghiêm ngặt state machine: `queued → running → waiting_approval → running → succeeded/failed/cancelled`.
- Trạng thái checkpoint LangGraph được lưu bền vững vào PostgreSQL.
- Worker thực hiện claim job bằng cơ chế leasing có thời hạn (`leased_until`) và xử lý timeout.

**PASS G7:** Run chỉ thực thi dựa trên `frozen_spec` bất biến đã publish; token usage được tối ưu hóa nhờ on-demand skill loader; state transition atomic.

---

## 10. RT-02 — Tool Gateway, MCP Client & 12-Step ToolDispatcher

### 10.1. Canonical 12-Step ToolDispatcher Pipeline

Mọi lượt gọi tool (dù là builtin, http hay MCP) đều bắt buộc đi qua pipeline 12 bước tuần tự không thể đảo ngược:

```text
receive tool call
 -> 1. Resolve immutable tool definition / version
 -> 2. Validate input JSON Schema
 -> 3. Load trusted principal + workspace permissions
 -> 4. Check policy & grants (RBAC / ABAC)
 -> 5. Check approval policy (Side-effect / Risk level)
 -> 6. Verify decision references exact payload hash
 -> 7. Reserve durable operation_key / idempotency record in DB
 -> 8. Execute connector (builtin / MCP Client) with timeout & bounded retry
 -> 9. Reconcile ambiguous outcomes (network timeout, UNKNOWN status)
 -> 10. Write receipt & audit trail to governance.audit_events
 -> 11. Redact & sanitize output (strip internal credentials / PII)
 -> 12. Return sanitized result to Agent
```

### 10.2. Cơ chế Bảo mật SSRF cho MCP Client Adapter

Khi kết nối tới external MCP server hoặc HTTP tools, client kiểm tra IP đích trước khi mở socket:
- **Chặn Loopback:** `127.0.0.0/8`, `::1`.
- **Chặn Private Networks (RFC1918):** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.
- **Chặn Cloud Metadata Endpoint:** `169.254.169.254` (AWS, GCP, Azure metadata services).
- **Chặn IPv6 Link-Local:** `fe80::/10`.
- Nếu phát hiện vi phạm, ngắt kết nối lập tức và ném lỗi `McpSecurityError("SSRF_BLOCKED")`.

### 10.3. Cơ chế Chống Zip-Slip / Path Traversal cho Skill Resource Reader

Khi đọc file tài nguyên mẫu hoặc script trong thư mục Skill:
- Kiểm tra đường dẫn chuẩn hóa (`resolved_path`).
- Đảm bảo `resolved_path` nằm hoàn toàn bên trong thư mục gốc của Skill: `resolved_path.is_relative_to(skill_root_dir)`.
- Chặn đứng mọi nỗ lực khai thác đường dẫn `../../etc/passwd` hoặc traversal trong file zip.

**PASS G8:** ToolDispatcher thực thi đủ 12 bước; tấn công SSRF và Path Traversal bị chặn đứng 100% kèm audit log; thao tác ghi có tính idempotent tuyệt đối qua `(workspace_id, operation_key)`.

---

## 11. RT-03 — LangGraph, Human Approval (HITL) và Recovery

### 11.1. Checkpoint Bền vững và Human-in-the-Loop (HITL)
- Sử dụng PostgreSQL-backed checkpointer lưu trữ toàn bộ trạng thái thực thi của Graph theo `thread_id`.
- Khi gặp action có mức rủi ro cao hoặc yêu cầu phê duyệt theo policy, hệ thống gọi `interrupt()` để chuyển Run sang trạng thái `waiting_approval`.
- Tạo bản ghi trong `runtime.approval_requests` kèm `action_hash` đại diện cho payload chính xác.

### 11.2. Phê duyệt Atomic và Resume An toàn
- Quyết định phê duyệt được ghi nhận vào `runtime.approval_decisions` trong transaction có khóa `FOR UPDATE`.
- Kiểm tra `action_hash` khi resume để bảo đảm người phê duyệt duyệt đúng hành vi mà Agent sắp thực hiện.
- Khôi phục tiến trình qua `Command(resume=...)`.

### 11.3. Khả năng Phục hồi sau Restart (Fault Injection Test)
- Kịch bản: Khởi chạy Run -> Chờ duyệt -> Cố tình dừng tiến trình `agent-runtime` -> Khởi động lại -> Xác nhận Run vẫn ở trạng thái `waiting_approval` mà không mất mát dữ liệu -> Phê duyệt -> Graph tiếp tục chạy và hoàn tất thành công.

**PASS G9:** Không xảy ra duplicate execution khi restart hoặc retry; kết quả sau khi resume trùng khớp với phiên làm việc gốc.

---

## 12. OPS-01 — Observability, Evaluation, CI và Security Gates

### 12.1. Truy vết phân tán (Distributed Tracing)
- Gắn kết spans: `api.request → agent.run → skill.load → tool.authorize → approval.wait → tool.execute (MCP) → run.finish`.
- Định danh thống nhất: `trace_id`, `run_id`, `workspace_id`, `agent_version_id`.
- Tuyệt đối loại bỏ PII, secrets và token khỏi log attributes.

### 12.2. CI Quality Gates `G10`

Bộ kiểm thử tự động CI phải xác minh đầy đủ:
1. `test_skill_and_mcp_contracts.py`: Hợp đồng schema Skill, MCP, SSRF defense, Zip-Slip defense.
2. `test_cross_tenant_isolation.py`: Cô lập dữ liệu đa tenant trên toàn bộ 55 bảng RLS.
3. `test_agent_publish_and_run.py`: Chu trình đầy đủ từ publish bản bất biến đến kích hoạt run.
4. `test_resume_after_restart.py`: Kiểm thử khôi phục trạng thái sau sự cố tiến trình.

**PASS G10:** CI vượt qua 100% tests với 0 warnings bảo mật; static DDL checker xác nhận 56 bảng, 55 RLS, 10 triggers.

---

## 13. WEB-01 — Agent Studio & Management Console

Giao diện điều khiển tối thiểu cung cấp đầy đủ chức năng quản trị:
- **Agent Studio:** Soạn thảo draft spec, đính kèm Tools và Skills, kiểm tra xung đột revision, publish immutable version.
- **Skill Management Console:** Danh mục Skill của workspace, xem chi tiết `SKILL.md`, quản lý phiên bản, tải lên bundle.
- **MCP Connections Console:** Quản lý kết nối MCP Servers, kiểm tra kết nối, khám phá công cụ (Discovery), đồng bộ công cụ vào catalog.
- **Run Timeline & Approval Center:** Theo dõi tiến trình Run qua Server-Sent Events (SSE), phê duyệt hoặc từ chối các yêu cầu can thiệp HITL.

**PASS G11:** Thao tác End-to-End từ UI hoạt động mượt mà; không rò rỉ secret hoặc bypass authorization từ client.

---

## 14. RELEASE-01 — Pilot Release và Diễn tập Khôi phục

1. **Diễn tập Restore:** Xuất logical backup từ database hiện tại, phục hồi sang database mới, xác nhận nguyên vẹn 56 bảng, 55 RLS policies, 10 triggers.
2. **Quy trình Rollback:** Xác nhận khả năng rollback phiên bản ứng dụng mà không gây hỏng dữ liệu hoặc xung đột schema.
3. **Nghiệm thu Tổng thể:** Tất cả các Gate từ G0 đến G12 đều đạt trạng thái `PASS`.

---

## 15. Bảng Đối chiếu 56 Bảng Cơ sở dữ liệu V1.0

| STT | Schema | Tên Bảng | Vai trò & Đặc tính | RLS | Immutability Trigger |
|---|---|---|---|---|---|
| 1 | iam | tenants | Khách hàng thuê tổ chức | CÓ | - |
| 2 | iam | workspaces | Không gian làm việc logic | CÓ | - |
| 3 | iam | users | Người dùng toàn cầu (Global Identity) | KHÔNG | - |
| 4 | iam | memberships | Thành viên trong workspace & vai trò | CÓ | - |
| 5 | iam | service_accounts | Định danh máy cho tích hợp hệ thống | CÓ | - |
| 6 | catalog | agents | Thực thể Agent cấp cao & draft spec | CÓ | - |
| 7 | catalog | agent_versions | Phiên bản Agent đóng băng bất biến | CÓ | **CÓ** |
| 8 | catalog | agent_publications | Con trỏ môi trường phát hành (staging/prod) | CÓ | - |
| 9 | catalog | model_configs | Cấu hình LLM được phê duyệt | CÓ | - |
| 10 | catalog | tools | Công cụ đăng ký trong workspace | CÓ | - |
| 11 | catalog | tool_versions | Phiên bản Tool đóng băng bất biến | CÓ | **CÓ** |
| 12 | catalog | agent_tools | Khai báo công cụ đính kèm Agent | CÓ | - |
| 13 | catalog | agent_tool_bindings | Ràng buộc cụ thể phiên bản Agent & Tool | CÓ | - |
| 14 | catalog | workflows | Quy trình đồ thị Agent | CÓ | - |
| 15 | catalog | workflow_versions | Phiên bản đồ thị đóng băng bất biến | CÓ | **CÓ** |
| 16 | catalog | business_processes | Quy trình nghiệp vụ doanh nghiệp | CÓ | - |
| 17 | catalog | business_process_versions | Phiên bản quy trình đóng băng bất biến | CÓ | **CÓ** |
| 18 | catalog | digital_workers | Định danh nhân sự số vận hành | CÓ | - |
| 19 | catalog | **skills** | **Khai báo Skill (gói hướng dẫn & tài nguyên)** | CÓ | - |
| 20 | catalog | **skill_versions** | **Phiên bản Skill đóng băng bất biến (frozen_spec)** | CÓ | **CÓ** |
| 21 | catalog | **agent_skills** | **Ràng buộc liên kết giữa Agent và Skill** | CÓ | - |
| 22 | knowledge | knowledge_bases | Kho tri thức doanh nghiệp | CÓ | - |
| 23 | knowledge | datasets | Tập dữ liệu tài liệu | CÓ | - |
| 24 | knowledge | dataset_versions | Phiên bản tài liệu đóng băng bất biến | CÓ | **CÓ** |
| 25 | knowledge | documents | Tài liệu gốc được nạp | CÓ | - |
| 26 | knowledge | document_chunks | Đoạn văn bản sau khi chia nhỏ | CÓ | - |
| 27 | knowledge | embeddings | Vector nhúng phục vụ tìm kiếm ngữ nghĩa | CÓ | - |
| 28 | knowledge | agent_knowledge_bindings | Ràng buộc tri thức cho từng Agent | CÓ | - |
| 29 | runtime | runs | Phiên thực thi của Agent | CÓ | - |
| 30 | runtime | run_steps | Các bước thực thi chi tiết (Append-only) | CÓ | **CÓ** |
| 31 | runtime | run_events | Sự kiện tiến trình phục vụ SSE & Replay | CÓ | **CÓ** |
| 32 | runtime | approval_requests | Yêu cầu phê duyệt Human-in-the-Loop | CÓ | - |
| 33 | runtime | approval_decisions | Quyết định phê duyệt (Duyệt/Từ chối) | CÓ | - |
| 34 | runtime | tool_operations | Quản lý Idempotency cho các thao tác Side-effect | CÓ | - |
| 35 | runtime | jobs | Hàng đợi công việc cho background workers | CÓ | - |
| 36 | runtime | checkpoint_refs | Tham chiếu lưu trạng thái LangGraph checkpointer | CÓ | **CÓ** |
| 37 | runtime | threads | Luồng hội thoại logic | CÓ | - |
| 38 | runtime | thread_messages | Tin nhắn hội thoại | CÓ | - |
| 39 | runtime | conversation_contexts | Ngữ cảnh ghi nhớ ngắn hạn/dài hạn | CÓ | - |
| 40 | runtime | eval_runs | Kết quả đánh giá hiệu năng & chất lượng | CÓ | - |
| 41 | integration | webhooks | Cấu hình webhook nhận sự kiện ngoài | CÓ | - |
| 42 | integration | outbox_events | Transactional outbox đảm bảo phát sự kiện | CÓ | - |
| 43 | integration | api_keys | Khóa API truy cập nền tảng | CÓ | - |
| 44 | integration | **mcp_connections** | **Cấu hình kết nối Model Context Protocol Server** | CÓ | - |
| 45 | integration | **mcp_connection_secrets** | **Bảo mật bí mật kết nối MCP (Mã hóa)** | CÓ | - |
| 46 | integration | **mcp_tool_mappings** | **Ánh xạ công cụ MCP vào Catalog hệ thống** | CÓ | - |
| 47 | governance | policies | Chính sách quản trị & an toàn | CÓ | - |
| 48 | governance | policy_rules | Quy tắc kiểm soát hành vi chi tiết | CÓ | - |
| 49 | governance | audit_events | Nhật ký kiểm toán bất biến (WORM) | CÓ | **CÓ** |
| 50 | governance | cost_tracking | Theo dõi chi phí và token tiêu thụ | CÓ | - |
| 51 | ops | system_metrics | Chỉ số tải hệ thống | CÓ | - |
| 52 | ops | node_heartbeats | Nhịp tim các node tính toán | CÓ | - |
| 53 | ops | alerts | Cảnh báo vận hành | CÓ | - |
| 54 | ops | slo_tracking | Theo dõi cam kết chất lượng dịch vụ | CÓ | - |
| 55 | ops | incidents | Sự cố hệ thống cần can thiệp | CÓ | - |
| 56 | ops | circuit_breakers | Ngắt mạch tự động khi lỗi phụ thuộc ngoài | CÓ | - |

---

## 16. Danh sách kiểm tra nghiệm thu tổng thể (Master Release Gates)

- [ ] **G0 (Repo & Docker):** Môi trường sẵn sàng, Docker Compose cấu hình PostgreSQL 16 hợp lệ.
- [ ] **G1 (Database V1.0 Baseline):** PostgreSQL chấp nhận trọn vẹn **56 bảng**, 7 schemas (`iam=5, catalog=16, knowledge=7, runtime=12, integration=6, governance=4, ops=6`), toàn bộ FK composite hợp lệ.
- [ ] **G2 (Security & Isolation):** **55 bảng** tenant-scoped bật `FORCE ROW LEVEL SECURITY`, `iam.users` là global table duy nhất; **10 immutability triggers** chặn đứng hành vi sửa snapshot.
- [ ] **G3 (Migration & Disaster Recovery):** Alembic tracked baseline 56 bảng; diễn tập backup & restore thành công trên database độc lập.
- [ ] **G4 (API Contracts):** OpenAPI & JSON Schemas chuẩn hóa (bao gồm Skill Manifest và MCP Connection schemas); error envelope đồng nhất.
- [ ] **G5 (Authn/Authz):** JWT verification thành công; context tenant injection vào transaction; 0 rò rỉ qua connection pooling.
- [ ] **G6 (Registry & Immutability):** Đóng băng phiên bản Agent và Skill với mã băm `bundle_sha256`; optimistic revision chống race conditions.
- [ ] **G7 (Runtime Engine):** Nạp Skill on-demand giúp tối ưu hóa token; state machine đảm bảo chuyển trạng thái an toàn.
- [ ] **G8 (Tool Gateway & MCP):** 12-Step ToolDispatcher bảo đảm xác thực quyền, idempotency reservation; bộ lọc SSRF chặn loopback/private/metadata IP; bộ đọc Skill chặn Zip-Slip/Path Traversal.
- [ ] **G9 (Human-in-the-Loop & Recovery):** Khôi phục chính xác trạng thái sau restart tiến trình; phê duyệt atomic dựa trên exact payload hash.
- [ ] **G10 (CI/CD & Observability):** Toàn bộ test suites vượt qua 100%; distributed tracing không để lộ secret.
- [ ] **G11 (Console UI):** Giao diện Web Console hỗ trợ quản lý Agent, Skill, MCP, Runs và Approvals hoàn chỉnh.
- [ ] **G12 (Pilot Decision):** Đạt tiêu chuẩn nghiệm thu KLTN VinAI theo đúng kiến trúc thiết kế chuẩn mực.
