# SPEC-DEPLOY-001 — Production Infrastructure, CI/CD, Migration & Disaster Recovery
**Status:** IMPLEMENTATION SPEC / NOT VERIFIED · **Version:** 1.0 (2026-10-30) · **Database:** `DB-BASELINE-56` (target) · **Priority:** P2 hardening; P0 CI & secrets guardrails · **Owner:** Platform / DevOps / Security

## 1. Mục tiêu, phụ thuộc và ranh giới

Thiết lập môi trường dev→staging→production cho studio-web, control-api, agent-runtime, worker (khi dùng), PostgreSQL, Qdrant, object storage và optional Redis. Xây CI/CD, IaC, secret management, database migrations, RLS validation, backup/restore, rollout/rollback, SLO và incident response. MVP có local Docker Compose/Devcontainer nhưng production phải có IaC được phê duyệt và review.

**Phụ thuộc:** All service specs + `CANONICAL_DATABASE_56.md` / `DB-56_MIGRATION_IMPLEMENTATION.md`

**Không nằm trong scope:** Premature microservices/Kubernetes mandate for MVP, insecure public MCP, unscoped prod shell access, SQL schema rewrite of 0001, “exactly-once” side effect guarantee without downstream idempotency, claiming disaster recovery from having a backup file without restoring it.

## 2. Mapping sang CSDL 56 bảng

Baseline đích: 56 domain tables trong 7 schemas (`iam=5,catalog=16,knowledge=7,runtime=12,integration=6,governance=4,ops=6`). **No additional domain tables without ADR.** Tracking/checkpoint tables do framework provision nếu có phải khai báo rõ trong inventory; migrations ledger (Alembic/version table) không được nhập nhằng với domain table count.

Authoritative responsibilities: `integration.outbox_events/inbox_receipts` reliable messaging, `runtime.jobs` durable queue, `runtime.checkpoint_refs` pointer to checkpoint saver, `governance.audit_events` compliance, `ops.model_usage` cost metering, `governance.retention_policies` deletion/hold.

Deployment-managed datastores: Postgres volumes+backups, Qdrant collections/snapshots+payload indexes, object storage buckets/versioning, Redis ephemeral cache only unless configured for explicit durability with ADR.

## 3. Luồng hoạt động và domain invariants

1. Inventory repo dependencies, pinned runtime/SDK versions and migration head; generate SBOM, validate license/security posture; feature flags default deny external writes.
2. CI pipeline: format/lint/typecheck → unit → schema/OpenAPI compatibility → integration (Postgres non-bypass RLS, Qdrant, object store) → static security + dependency scan → controlled E2E → build signed container artifacts.
3. Migration pipeline runs expand-first additive changes; pause on destructive ALTER/DROP; verify 50+Skill3+MCP3 and catalog/constraints/RLS after migration. Never modify applied 0001; require migration checksum, staging rehearsal, lock/timeout budget.
4. Deploy stateless control-api/runtime behind HTTPS, readiness checks after dependency initialization. `runtime.jobs` claims use lease/reconcile on crash, not ephemeral in-memory tasks. Checkpointer has durable store configured and restored in DR.
5. Separate environment-specific secret references and service identities; deny runtime DDL/write to catalog version tables; network egress allowlists for providers and MCP servers. Rotation and emergency revocation tested.
6. Rollout through canary/blue-green/rolling depending platform; DB migration backward compatibility mandatory; rollback app version while DB schema remains additive (forward repair for bad migrations).
7. Backup: PostgreSQL PITR; object storage retention/versioning; Qdrant snapshots or deterministic rebuild; secret/KMS recovery; cross-resource restoration validation with traceable point-in-time consistency.
8. Failure drills: worker kill during approval/writes, DB unavailable, Qdrant stale, LLM provider outage, MCP unreachable; use Tool Gateway reconciliation for `unknown` side effects. No silent replay of high-impact actions.
9. Define SLOs *after measuring workload* (run success rate, queue latency, approval resume, tool error, security policy denial), alert thresholds and incident escalation owner.
10. Production go-live requires signed release checklist with measured PASS from staging and rollback rehearsal.

## 4. API contracts & authorization

Operations-only routes should not be user-accessible: `GET /health/live`, `/health/ready` via private ingress; `/metrics` behind internal auth. Admin deployment API, if created, uses `deploy:manage` and separate control-plane role with audit.

Pipeline contracts: `MigrationReport {from,to,tableCount,rlsVerified,constraintsVerified}`, `ReleaseManifest {gitSha,imageDigests,contractVersion,migrationHead,environment,approvals}`, `RestoreEvidence {backupId,restoreTime,checksum,drillResult}`. They are artifact files in CI/S3, NOT new Postgres domain tables.

Quy tắc API chung: `/api/v1/workspaces/{workspace_id}/...` (trừ endpoints quản trị/global có scope riêng); tenant rút từ token đã xác minh; pagination cursor; `requestId/traceId`; validation 422; 401/403/404(ẩn cross-tenant)/409/429/503 có mã lỗi ổn định. API routes là **đề xuất contract** cho đến khi `contracts/openapi/control-api.openapi.yaml` được duyệt. Không tạo endpoints mới nếu tương đương đã tồn tại.

## 5. Structured contracts, validation và trạng thái

Required controlled configuration: `DATABASE_URL` secret ref, `QDRANT_ENDPOINT` allowlisted, `OBJECT_STORAGE_BUCKET`, `CHECKPOINT_BACKEND`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `SECRET_MANAGER_REF`, `MCP_EGRESS_POLICY`, feature flags `ENABLE_MCP_WRITE=false`, `ENABLE_SKILL_SCRIPTS=false` by default. Config loaders reject unknown unsafe values, store environment schema in repo. No .env examples with real secrets. Production image built reproducibly with pinned lock files and non-root users.

## 6. File/module delta (đề xuất; kiểm tra repo thật trước)

```text
infra/{docker-compose.dev.yml,containers/,terraform/,kubernetes/,policies/,monitoring/,backup/}
.github/workflows/{ci.yml,db-migration.yml,release.yml,security.yml}
scripts/{verify_56_tables.py,verify_rls.py,smoke_e2e.py,backup_restore_test.sh}
contracts/operations/{release-manifest.schema.json,migration-report.schema.json}
docs/08-operations/{RUNBOOK.md,RESTORE.md,INCIDENT_RESPONSE.md,SLO.md,RELEASE_CHECKLIST.md}
tests/{integration/infra,e2e/failover_recovery.py,security/tenant_roles.py}
```

## 7. Security, privacy và kiểm soát quyền

Workload identity for each service, read-only deploy tokens, signed artifacts, per-environment secrets and egress restrictions. Image runs as non-root, readonly root FS where practical; filesystem/memory/CPU limits; protect metadata services and internal DNS from tool/MCP SSRF. Database RLS tests must use non-`BYPASSRLS` roles; security must also cover object store, Qdrant, cache and queues. Infrastructure logs/traces redact LLM messages, PII and tool args. Credential rotation + break-glass auditing documented.

## 8. Lỗi, retry, idempotency và recovery

Migration failure: stop rollout and use forward repair, never drop production tables blindly. DB failover: retry only safe reads/leased jobs, reconcile writes. Object and Qdrant outages: retrieval fails safe or returns explicit degraded status, no other-tenant fallback. Checkpoint backup restore verifies pending approvals and resume correctness; side-effect timeouts leave `tool_operations.status='unknown'` until reconciliation. Autoscale workers with lease behavior proven, not based on in-process state.

## 9. Test matrix / acceptance evidence

| ID | Drill | Required evidence |
|---|---|---|
| OPS-01 | Fresh DB migrate from empty | exactly 56 domain table target + schema inventory |
| OPS-02 | Non-bypass RLS by tenant | cross-tenant read/write denied |
| OPS-03 | Upgrade with old app canary | backward-compatible APIs/migrations |
| OPS-04 | Worker crash during approval | resume with exact checkpoint, no extra side effects |
| OPS-05 | Tool timeout after external write | `unknown` + reconcile, no duplicate write |
| OPS-06 | Restore Postgres/object/Qdrant | documented checksums and sample retrieval |
| OPS-07 | Delete/revoke secret | requests fail closed, trace redacted |
| OPS-08 | MCP SSRF and Skills script attempt | blocked by policy/network/sandbox controls |
| OPS-09 | Telemetry exporter outage | no secret leak, service behavior documented |
| OPS-10 | Deploy rollback | old binary works with expanded DB, migration head audited |
| OPS-11 | Qdrant stale index | reconciliation detects and prevents unsafe access |

## 10. Kế hoạch Pull Requests

- **OPS-PR01:** reproducible dev/test setup + locked deps + CI lint/unit/contracts.
- **OPS-PR02:** disposable integration environment + 56-table DB migration/RLS checks.
- **OPS-PR03:** container/IaC + separate service identities/secrets/egress.
- **OPS-PR04:** tracing/metrics/alerting + runbook + secure production configs.
- **OPS-PR05:** backup/restore, chaos/restart and staged rollout/rollback proof.
- **OPS-PR06:** production release gate, threat review, operations handoff.

## 11. Observability / metrics / operations

SLO dashboard for API success/latency, run & tool failure rates, queue age, checkpoint save lag, approval wait/resume latency, cost, storage usage, vector drift, RLS/policy denials, MCP health. Runbook links alerts to exact owner; do not use invented SLO numbers as production commitment.


## Cross-cutting rules (bắt buộc)

- **Source of truth:** `docs/00-governance/CANONICAL_DATABASE_56.md` và 50-table `references/ENTERPRISE_AGENT_PLATFORM_0001_BASELINE.sql`; 3 bảng Skill và 3 bảng MCP là migration mở rộng, **chưa xác nhận chạy thật**. Tổng 56 domain tables là TARGET, không phải production claim. Các bảng framework/checkpointer nội bộ nếu có phải kiểm kê và cách ly, không giả vờ nằm trong 56.
- **Tenant boundary:** derive `tenant_id` từ authenticated principal; xác minh `workspace_id`, ACL, RLS (khi tồn tại) bằng DB role không `BYPASSRLS`; không tin ID hoặc claim do model cung cấp. Object storage, cache, Qdrant, event streaming phải áp cùng scope.
- **Immutability:** AgentVersion, WorkflowVersion, SkillVersion, ToolVersion, eval dataset versions đã publish là immutable; run pin exact version. Nếu phải thêm cột/index/bảng mới, viết ADR + migration review; **không tự tạo bảng domain thứ 57**.
- **Authority:** LLM/Skill/RAG/MCP output là dữ liệu không đáng tin; không được tự tạo grants. Tool side effects bắt buộc đi qua `SPEC-TOOL-001` / policy `SPEC-SECURITY-001`; HITL qua `SPEC-WORKFLOW-001`.
- **Evidence:** code/DDL/documentation khác với triển khai chạy thật. Từng PR phải báo `PASS` / `FAIL` / `BLOCKED` / `NOT_RUN` cùng câu lệnh test và bằng chứng; không được ghi PASS chỉ vì đã viết mock hoặc file spec.
- **No duplicate services:** `control-api` là modular monolith, `agent-runtime` là execution plane; `studio-web` là UI. Tái sử dụng các module, contracts, migration tooling đang tồn tại trước khi tạo tên thư mục mới.

## Uniform coding-agent delivery contract

Mỗi PR phải giao: thay đổi file (paths thực tế), contracts thay đổi, migration impact (hoặc `none`), các kiểm thử với output, backward compatibility, security findings, rollback/recovery và danh sách TODO chưa xong. Nếu repo local khác layout đề xuất thì dùng module tương đương và ghi mapping; không tạo song song. Thực hiện một PR/phạm vi mỗi lượt; trạng thái dự án được kiểm tra **read-only** trước khi sửa.

## Technical references

- https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- https://qdrant.tech/documentation/manage-data/indexing/
- https://opentelemetry.io/docs/specs/semconv/
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
