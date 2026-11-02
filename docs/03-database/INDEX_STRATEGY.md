# DATABASE INDEXING STRATEGY — BASELINE V2.0 (56 TABLES)

## 1. Nguyên tắc Đánh chỉ mục
1. **Tenant Isolation Indexing:** Mọi bảng tenant-scoped đều có composite index bắt đầu bằng `(tenant_id, workspace_id, ...)`.
2. **Foreign Key Coverage:** 100% composite FKs được hỗ trợ bởi unique index tương ứng ở bảng cha để tối ưu hóa truy vấn JOIN.
3. **Queue & Job Optimization:** Chỉ mục có điều kiện (`partial index`) cho hàng đợi công việc `runtime.jobs`:
   ```sql
   CREATE INDEX idx_jobs_pending ON runtime.jobs (tenant_id, workspace_id, available_at)
   WHERE status = 'queued';
   ```
4. **Idempotency Lookup:** Bảng `runtime.tool_operations` có UNIQUE index trên `(tenant_id, workspace_id, operation_key)` bảo đảm thời gian tra cứu $O(1)$ khi kiểm tra duplicate side-effect.
