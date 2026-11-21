# Công cụ và kỹ thuật triển khai

## Chiến lược thử nghiệm
- **PostgreSQL thực:** Testcontainers / docker compose test DB. Nên dùng mỗi test một schema/db độc lập hoặc transaction cleanup khi phù hợp.
- **Concurrent schedule:** hai connections độc lập + barriers để ép T1/T2 vào cùng điểm, không dựa vào `sleep()` ngẫu nhiên; ghi lịch và SQLSTATE.
- **Oracle:** SQL queries kiểm tra invariant sau khi tất cả transactions đã commit/rollback và message queue xử lý xong.
- **Rollback:** nếu có side effects qua broker/API, DB rollback không tự undo external side effect. Phải kiểm thử outbox/inbox tương ứng.

## PostgreSQL RLS kiểm thử đúng role
```sql
-- Chỉ chạy trên DB test đã cấu hình sẵn policies.
BEGIN;
SET LOCAL ROLE app_runtime_test;
SET LOCAL app.tenant_id = 'tenant-a';
SELECT id, tenant_id FROM documents; -- assert: no tenant-b rows
ROLLBACK;
```
Đảm bảo `app_runtime_test` **không** có `BYPASSRLS`, không là owner mặc định; `SET ROLE` có thể bị hạn chế bởi privilege, nên cấu hình trong fixture test. Nếu RLS dựa JWT context custom GUC, reset per transaction/pool; test với 2 connections.

## Concurrent invariant example
`stock_available >= 0` và `fulfilled_qty <= stock_initial`. Tạo 2 orders đồng thời tranh 1 item; assert đúng một order được fulfill hoặc cơ chế queue hợp lệ; giải thích kỳ vọng theo business rule.

## Migration risk
Không mặc định downgrade an toàn. Test snapshot trước/sau bằng dataset synthetic; large backfills cần kiểm tra lock duration và idempotent resume trong staging.
