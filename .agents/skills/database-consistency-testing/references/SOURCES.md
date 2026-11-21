# Nguồn chính thức — database-consistency-testing

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## PostgreSQL Transaction Isolation
- URL: https://www.postgresql.org/docs/current/transaction-iso.html
- Dùng để: Hiểu READ COMMITTED, REPEATABLE READ, SERIALIZABLE, serialization_failure.

## PostgreSQL Row Security Policies
- URL: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- Dùng để: Policy, owner/superuser bypass, FORCE RLS.

## PostgreSQL Explicit Locking
- URL: https://www.postgresql.org/docs/current/explicit-locking.html
- Dùng để: Row/table locks, deadlocks và conflict.

## Alembic official docs
- URL: https://alembic.sqlalchemy.org/en/latest/
- Dùng để: Migration scripts, online/offline migration environment.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
