---
name: database-consistency-testing
description: "Kiểm thử tính nhất quán dữ liệu và hành vi database dưới transaction, concurrency, retry, migration và multi-tenancy. Dùng khi cần kiểm tra PostgreSQL ACID/isolation, lost update, deadlock, idempotency, outbox, event replay, Saga compensation, RLS, constraints và dữ liệu sau lỗi; tạo invariant, test đồng thời và bằng chứng DB."
metadata:
  language: "vi"
  version: "1.0.0"
---
# Database Consistency Testing — dữ liệu đúng khi có lỗi và đồng thời

## Kích hoạt và phạm vi
**Khi nào dùng:** Yêu cầu test transaction, isolation, concurrency, migration, RLS, idempotency, outbox/inbox, event redelivery hoặc Saga; điều tra data corruption, duplicate record, phantom state.

**Không dùng để:** Không phải benchmark tốc độ DB hay review SQL style đơn thuần; không chạy test phá dữ liệu trên production.

**Mục tiêu độc lập:** Chứng minh các data invariants được duy trì qua giao dịch, cạnh tranh, retry và thay đổi lược đồ; ưu tiên kiểm tra trên DB engine thật.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- DDL/schema/migrations, SQL/ORM, repository/service code, engine/version thực tế.
- Các data invariants đã xác nhận: số dư không âm, tổng ledger, ownership/tenant, unique request ID, state transitions...
- Test DB có thể reset, danh tính role không bypass RLS, cách chạy workers/consumer nếu có.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Đọc schema, indexes, unique/FK/check constraints, transaction boundaries và connection/session lifecycle. Nêu rõ invariant với cách đo DB-level.
2. Tạo dữ liệu tối thiểu nhưng có thể tái lập; chạy trên DB engine/version khớp production khi khả thi, dùng isolated schema/database/container.
3. Thiết kế lịch thực thi hai hoặc nhiều transaction T1/T2 cho lost update, write skew, deadlock, serialization failure; chỉ kỳ vọng hiện tượng theo isolation thực tế.
4. Kiểm tra rollback khi exception, commit một phần, retry lỗi 40001/40P01 có giới hạn, timeout, optimistic/pessimistic locking.
5. Xác minh idempotency key, deduplication, outbox atomicity, inbox, ít nhất một lần delivery và compensation của Saga khi liên quan.
6. Test migration: upgrade, constraints/backfill, rolling-version compatibility nếu có; rollback chỉ khi migration hỗ trợ thực tế, tuyệt đối không giả định downgrade an toàn.
7. Với RLS, chạy bằng application role thật, không dùng superuser/owner/BYPASSRLS; test SELECT/INSERT/UPDATE/DELETE trên 2 tenant và connection pooling.
8. Dùng SQL assertions + app assertions sau khi mọi tác vụ kết thúc; dọn dữ liệu, khôi phục DB, báo lỗi kèm lịch thực thi và trạng thái cuối.

## Nguyên tắc an toàn và tính đúng đắn
- Không dùng production DB; không chạy DROP/TRUNCATE/seed phá hủy nếu chưa được xác nhận môi trường cách ly và kế hoạch phục hồi.
- Không test RLS bằng owner hoặc superuser rồi kết luận hệ thống cô lập tenant; chú ý FORCE ROW LEVEL SECURITY và BYPASSRLS.
- Không thay thế real PostgreSQL bằng SQLite cho các khẳng định về isolation, locks, JSONB, RLS hoặc constraints riêng PostgreSQL.
- Không đánh đồng “request thành công” với invariant DB đúng; xác minh side effects sau retry, failure và concurrent execution.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `data-invariants.md`: định nghĩa, nguồn nghiệp vụ, SQL oracle.
- `tests/` cho transaction/concurrency/RLS/migration theo conventions repo.
- `consistency-report.md`: schedules, engine/version, observed DB states, deadlocks/retries, cleanup.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Các invariant chính có kiểm tra tự động có thể chạy lặp lại.
- Thử tối thiểu một lịch cạnh tranh có ý nghĩa đối với mỗi critical write path.
- Trạng thái DB sau exception/retry được đối chiếu kỳ vọng, không chỉ HTTP status.
- RLS được kiểm tra với role production-equivalent hoặc đánh dấu BLOCKED.

## Mẫu lời gọi skill
> Hãy sử dụng `database-consistency-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
