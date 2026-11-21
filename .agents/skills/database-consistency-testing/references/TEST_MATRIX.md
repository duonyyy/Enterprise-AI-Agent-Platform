# Test matrix — database-consistency-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `DB-01` | Atomic rollback | Fail giữa 2 SQL statements trong một transaction | Không có partial write khi rollback | P0 |
| `DB-02` | Concurrent update | T1/T2 cùng đọc và sửa record phiên bản cũ | Không lost update trái invariant; conflict/retry hợp lệ | P0 |
| `DB-03` | Unique idempotency | Hai request same workspace+key đồng thời | Một nghiệp vụ/side effect; result ổn định | P0 |
| `DB-04` | Retry serialization | Gây PostgreSQL SQLSTATE 40001 trên isolated DB | Bounded retry/abort; invariant không hỏng | P0 |
| `DB-05` | Deadlock | Hai transaction lock resource ngược thứ tự | Deadlock được xử lý, không partial commit | P1 |
| `DB-06` | Outbox atomicity | Transaction ghi business row + outbox rồi lỗi | Commit cả hai hoặc rollback cả hai | P0 |
| `DB-07` | Message redelivery | Consumer nhận same message_id ≥ 2 lần | Business effect idempotent; inbox/dedupe đúng | P0 |
| `DB-08` | Tenant RLS | role ứng dụng tenant A truy vấn/ghi row tenant B | SELECT/INSERT/UPDATE/DELETE bị giới hạn; pool reset context | P0 |
| `DB-09` | Migration constraint | Apply migration lên dữ liệu cũ và dữ liệu biên | Schema đúng, backfill không mất dữ liệu, restartable khi thiết kế | P1 |
| `DB-10` | Saga compensation | Payment ok nhưng reservation thất bại (sandbox) | State cuối đúng compensation policy, no double reversal | P0 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
