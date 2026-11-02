# DATABASE BASELINE 56 — Quy ước duy nhất cho Enterprise AI Agent Platform

- Mã: `DB-BASELINE-56`
- Phiên bản: `1.0` (2026-10-30)
- Trạng thái: **TARGET DESIGN / NOT VERIFIED ON LIVE DATABASE**
- Phạm vi: **Enterprise AI Agent Platform độc lập**; KHÔNG liên quan dự án khác.

## 1. Quyết định đã chốt

**Đích là 56 bảng vật lý trong 7 PostgreSQL schemas** (`iam`, `catalog`, `knowledge`, `runtime`, `integration`, `governance`, `ops`), theo công thức: 50 bảng từ `ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql` + 3 Skill + 3 MCP.

Tại thời điểm viết, 50 bảng có khai báo DDL trong baseline SQL nhưng **chưa có bằng chứng chạy migration thành công**; sáu bảng Skill/MCP mới có trong hai change request, **chưa là DDL đã áp dụng**. Không báo `IMPLEMENTED` hoặc `PASS` chỉ vì đã có tài liệu.

Đây là **cấu trúc đích**, không bắt buộc tạo đồng thời toàn bộ 56 bảng trong một PR. Áp dụng theo migration thứ tự có thể kiểm thử.

## 2. Danh mục 56 bảng chính thức

| Schema | 50 bảng nền tảng | Bảng bổ sung | Tổng đích |
|---|---:|---:|---:|
| `iam` | 5 | 0 | 5 |
| `catalog` | 13 | 3 | 16 |
| `knowledge` | 7 | 0 | 7 |
| `runtime` | 12 | 0 | 12 |
| `integration` | 3 | 3 | 6 |
| `governance` | 4 | 0 | 4 |
| `ops` | 6 | 0 | 6 |
| **Tổng** | **50** | **6** | **56** |

### `iam` — 5
`tenants`, `users`, `workspaces`, `memberships`, `service_accounts`.

### `catalog` — 16
`model_configs`, `business_processes`, `workflows`, `workflow_versions`, `tools`, `tool_versions`, `agents`, `agent_versions`, `agent_publications`, `tool_grants`, `digital_workers`, `agent_tool_bindings`, `agent_knowledge_bindings`, **`skills`**, **`skill_versions`**, **`agent_skill_bindings`**.

### `knowledge` — 7
`bases`, `base_grants`, `documents`, `document_versions`, `chunks`, `vector_refs`, `ingestion_jobs`.

### `runtime` — 12
`conversations`, `messages`, `runs`, `work_items`, `run_steps`, `run_events`, `checkpoint_refs`, `memory_records`, `approval_requests`, `approval_decisions`, `tool_operations`, `jobs`.

### `integration` — 6
`connections`, `outbox_events`, `inbox_receipts`, **`mcp_discovery_snapshots`**, **`mcp_discovered_items`**, **`mcp_tool_mappings`**.

### `governance` — 4
`policy_sets`, `policy_versions`, `audit_events`, `retention_policies`.

### `ops` — 6
`model_usage`, `eval_datasets`, `eval_dataset_versions`, `eval_cases`, `eval_runs`, `eval_results`.

## 3. Owners, giới hạn và phụ thuộc

| Bảng / nhóm | Owner ghi | Owner đọc và điều kiện |
|---|---|---|
| `catalog.agents`, `agent_versions`, `agent_publications` | Control API / Agent Registry | Runtime chỉ đọc version đã publish, exact ID |
| `catalog.skills`, `skill_versions`, `agent_skill_bindings` | Control API / Skills | Runtime chỉ đọc metadata và artifact đã pin; không tự publish |
| `catalog.tools`, `tool_versions`, `tool_grants`, `agent_tool_bindings` | Control API / Tool Registry | Tool Gateway đọc để authorize; MCP không tạo executor riêng |
| `integration.connections`, `mcp_*` | Control API / MCP | Runtime chỉ đọc approved connection/tool mapping và secret_ref thông qua gateway |
| `runtime.runs`, `run_steps`, `run_events`, `checkpoint_refs`, `tool_operations`, `jobs` | Execution / Runtime | Control API chỉ đọc/ủy quyền các lệnh quản trị; không sửa graph state tùy ý |
| `runtime.approval_requests`, `approval_decisions` | Approval application service (Control API) | Runtime chỉ consume quyết định được xác thực và resume |
| `governance.*` | Security/Governance service | Append audit, policy version; không đưa secret/raw prompt vào audit |
| `knowledge.*` | Knowledge service | Runtime truy xuất có tenant, workspace, ACL; vector DB không là system-of-record |

**Không có bảng thứ 57** cho Tool Registry, Agent registry, MCP Server Registry riêng hoặc workflow checkpoint snapshot nếu chưa qua ADR và migration review. `integration.connections` vốn đã là MCP Connection Registry; `catalog.tools` là Tool Registry dùng chung.

## 4. Migration policy bắt buộc

1. `0001_baseline.sql`: 50 bảng gốc. **KHÔNG viết lại** nếu đã từng áp dụng.
2. Migration mới từ `CR-SKILL-001`: +3 bảng `catalog.*`.
3. Migration mới từ `CR-MCP-001`: +3 bảng `integration.*`.
4. Đi cùng RLS `ENABLE` và `FORCE`, `GRANT` tối thiểu, index phục vụ workload, FK cùng `(tenant_id,workspace_id)`, trigger immutability theo thiết kế.
5. `agent_skill_bindings` phải pin **version cụ thể**, không dùng `latest`; MCP mappings phải pin **tool_version_id** và schema hash đã duyệt.
6. Không dùng user có `BYPASSRLS`, superuser hoặc role migration để chứng minh tenant isolation.
7. Không tạo credential plaintext trong PostgreSQL, chỉ giữ `secret_ref`.
8. Phân biệt `MIGRATION_AUTHORED`, `MIGRATION_APPLIED_TEST`, `RLS_TESTED`, `PRODUCTION_DEPLOYED`.

## 5. Verification commands (chạy với test DB)

```sql
SELECT table_schema, count(*) AS tables
FROM information_schema.tables
WHERE table_type='BASE TABLE'
  AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
GROUP BY table_schema ORDER BY table_schema;

SELECT count(*) AS total_tables
FROM information_schema.tables
WHERE table_type='BASE TABLE'
  AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops');

SELECT schemaname, tablename, rowsecurity, forcerowsecurity
FROM pg_tables
WHERE schemaname IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
ORDER BY schemaname,tablename;
```

Chỉ kết luận **56 bảng hiện hữu** khi `total_tables=56` ở DB thực tế và danh mục bảng khớp đích; test còn phải kiểm tra migrations, runtime roles, RLS và cross-tenant access. `iam.users`/`iam.tenants` là các bảng identity toàn cục có quyền truy cập riêng; không áp đặt một predicate `tenant_id` cho bảng không có cột đó.

## 6. Những thay đổi bắt buộc cho specs và coding agent

- Mọi spec mới ghi `DB-BASELINE-56` làm baseline đích.
- Không đọc con số 50 trong Repository v2/Database v1 thành số bảng cuối cùng; đó là **historical baseline**, cần cross-reference phần delta Skill/MCP.
- Mọi migration phải so sánh schema live với migrations history trước khi viết: nếu 56 bảng đã tồn tại, chỉ audit, **không tạo lại**.
- Không bổ sung SQL schema tùy tiện ngoài các bảng đã chốt; thay đổi cấu trúc cần migration additive, ticket và ADR.
- Với mọi claims như `MCP_APPROVED`, `SKILL_PUBLISHED`, `AGENT_EXECUTED`, cần unit/integration/e2e evidence.

## 7. Tham chiếu kỹ thuật chính thức

- PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- PostgreSQL CREATE POLICY: https://www.postgresql.org/docs/current/sql-createpolicy.html
- MCP 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28
- Agent Skills: https://agentskills.io/specification
