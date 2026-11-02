# DB-56 — Migration Playbook: 50 Core + 3 Skill + 3 MCP

**Status:** PROPOSED / NOT APPLIED · **Baseline authority:** `CANONICAL_DATABASE_56.md`

## 1. Thứ tự migration và nguồn thiết kế

| Sequence | Đầu vào | Target count | Dependency |
|---|---|---:|---|
| `0001` | `ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql` | 50 | DB trống, migration owner |
| `0002` | `CR-SKILL-001`, §7 | 53 | `catalog.agents`,`catalog.agent_versions`, `iam.workspaces` |
| `0003` | `CR-MCP-001`, §6 | 56 | `integration.connections`, `catalog.tool_versions` |

Tuyệt đối **không** chỉ chỉnh số 50 thành 56 trong tài liệu. Cần viết migrations **forward-only** cho sáu bảng mới. Đừng sửa `0001` nếu đã từng áp dụng. Không tự ý tạo 6 bảng bằng ORM `create_all` rồi đánh dấu Alembic migration đã chạy.

## 2. `0002` — Skill tables, FK & constraints

1. `catalog.skills`: `tenant_id`,`workspace_id`,`id`, slug, display_name, draft_skill_md, draft_manifest JSON object, draft_bundle_ref, revision > 0, status active/disabled/archived, created_by, created_at/updated_at.
2. `catalog.skill_versions`: `tenant_id`,`workspace_id`,`id`,`skill_id`, version > 0, schema_version, manifest JSON object, skill_md, immutable bundle_ref + sha256, published_by, published_at.
3. `catalog.agent_skill_bindings`: `tenant_id`,`workspace_id`,`id`,`agent_version_id`,`skill_id`,`skill_version_id`,created_at.
4. UNIQUE as requested: `(tenant,workspace,slug)` for skills; `(tenant,workspace,skill_id,version)` for versions; `(tenant,workspace,agent_version_id,skill_id)` for bindings; `(tenant,workspace,id)` on all 3.
5. FK: each `(tenant,workspace)` to workspaces, `skill_versions.(tenant,workspace,skill_id)` to skills; bindings `(tenant,workspace,agent_version_id)` to agent_versions, `(tenant,workspace,skill_id,skill_version_id)` to skill_versions.
6. For composite FK to `(tenant,workspace,skill_id,skill_version_id)` ensure a **matching UNIQUE** candidate key on `skill_versions` `(tenant_id,workspace_id,skill_id,id)`. Without this, migration will fail.
7. `skill_versions` and published bindings immutable by trigger/permissions. Reuse proven `governance.reject_immutable_mutation()` only when its API and effect are verified in baseline.
8. Add access path indexes for `(tenant_id,workspace_id,status,created_at)` for skills and pinned version lookup; avoid duplicating implicit UNIQUE/PK indexes.
9. `ENABLE/FORCE RLS`, tenant policies and GRANT separately for manager vs runtime read roles. Test both same-tenant different workspace and cross-tenant IDs.

## 3. `0003` — MCP tables, FK & constraints

1. `integration.mcp_discovery_snapshots`: `(tenant_id,workspace_id,id,connection_id)` reference to `integration.connections`; protocol_version, protocol_era, server_capabilities, catalog_sha256, status, captured_at.
2. `integration.mcp_discovered_items`: `(tenant_id,workspace_id,id,snapshot_id,connection_id)` with `item_kind`, remote_key, descriptor JSON, sha hashes; FK back to snapshot using `(tenant,workspace,snapshot_id,connection_id)`.
3. `integration.mcp_tool_mappings`: `(tenant_id,workspace_id,id,connection_id,remote_tool_name,discovery_item_id,tool_version_id,approved_schema_sha256,status,reviewed_by,reviewed_at,created_at)`.
4. FK to connections, discovered items `(tenant,workspace,connection_id,discovery_item_id)` and `catalog.tool_versions(tenant,workspace,id)`; check FK targets have matching UNIQUE indexes.
5. UNIQUE partial index where `status='approved'` on `(tenant,workspace,connection,remote_tool_name)` so one approved remote-name mapping is active.
6. Review must verify `item_kind=tool`, remote name, approved schema hash and `tools.kind=mcp` **transactionally**; FK does not guarantee these semantic checks.
7. Keep snapshot records immutable after completion and create new snapshots on refresh; mapping state can change approved → stale/revoked with audit.
8. Enable RLS + GRANT with non-bypass roles; index latest discovery, item lookup, approved mapping lookup. Do not reimplement `catalog.tools` or `integration.connections`.

## 4. RLS and security requirements

Use `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on six new tables. Tenant-context pattern with `current_setting('app.tenant_id',true)` works only for tenant-scoped rows and **does not authorize workspace-level access**; APIs must check membership. App DB role cannot be owner/superuser/BYPASSRLS; `SET LOCAL` tenant context from authenticated principal in same transaction. Custom GUC cannot protect against arbitrary SQL injection; use parameterized queries and least privilege.

Untrusted connections: never store secrets in JSON, no raw resource URLs from model become trusted. Schema drift must revoke mapping before the next new call. Published skill artifact bundles use immutable object key, matching SHA digest; files do not execute in MVP.

## 5. Migration test procedure — required on disposable PostgreSQL

1. Create empty test database and run original baseline `0001` via migration owner; inspect errors, then assert **50** domain tables.
2. Run Skill migration `0002`; assert **53** and the 3 exact names.
3. Run MCP migration `0003`; assert **56** and the 3 exact names.
4. Check FKs, UNIQUE constraints, partial unique indexes, and owner roles; count RLS coverage on all tenant-scoped tables.
5. Insert fixtures: tenants A/B, workspaces A1/A2, agents and versions, skills + versions, tools/MCP connections. Try illegal cross-tenant and same-tenant cross-workspace inserts/reads.
6. Test published immutability (UPDATE/DELETE fail) and version pin consistency. Test schema mismatch in MCP mapping (must not be approved).
7. Validate `alembic upgrade head` on fresh DB and a DB carrying `0001`; `alembic downgrade` is not required for destructive actions, adopt forward fix migration.
8. Restart test DB and verify data persists; run application roles for denial checks, not migration role.
9. Generate a machine-readable report with `migration_revision`, `table_count`, `failed_assertions`, `role_test_results`, commit SHA, runtime versions and timestamps.

```sql
SELECT table_schema, COUNT(*) AS domain_tables
FROM information_schema.tables
WHERE table_type='BASE TABLE'
AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
GROUP BY table_schema ORDER BY table_schema;

SELECT COUNT(*) AS total_domain_tables
FROM information_schema.tables
WHERE table_type='BASE TABLE'
AND table_schema IN ('iam','catalog','knowledge','runtime','integration','governance','ops');
```

`total_domain_tables=56` is **necessary but not sufficient**; compare names to manifest and check security. Framework-managed private tables must be counted separately (e.g. `langgraph` saver namespace, if created), not misrepresented as domain tables.

## 6. Exit gate

- `0001` + `0002` + `0003` apply without error on clean test DB; schema/name match target 56; app roles have no bypass.
- Negative RLS, FK, concurrent publish and idempotent reconciliation tests are green.
- Migration scripts and schema versions are committed; data dictionary updated to include the six new tables.
- **Until these tests actually run, keep status `NOT_VERIFIED` and never claim database is production-ready.**
