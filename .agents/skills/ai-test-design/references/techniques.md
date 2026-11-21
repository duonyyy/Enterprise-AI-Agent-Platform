# Kỹ thuật sinh test case theo tình huống

Chỉ chọn kỹ thuật khi có dấu hiệu phù hợp trong đặc tả. Những ví dụ dưới đây minh họa cách nghĩ, không phải expected result mặc định cho mọi ứng dụng.

## 1. Equivalence partitioning / boundary value

Đặc tả giả định: trường `quantity` là số nguyên từ 1 đến 100 (inclusive).

- Class hợp lệ: 1–100; đại diện 50.
- Class không hợp lệ: <=0, >=101, non-integer, missing, null.
- Boundary: 0, 1, 2, 99, 100, 101 (chọn biến thể tối thiểu hữu ích).
- Oracle: kiểm tra việc chấp nhận/từ chối, lỗi contract, không có side effect khi từ chối.
- Không mặc định HTTP status nếu contract không quy định.

## 2. Decision table

Nghiệp vụ giả định: chỉ cho `publish` khi người dùng có quyền và nội dung hợp lệ.

| Has publish permission | Valid content | Expected business outcome |
|---|---|---|
| true | true | publish succeeds |
| true | false | rejected due to content validation |
| false | true | rejected due to authorization |
| false | false | rejected; error precedence NEEDS_DECISION |

Khi có nhiều rules, ghi điều kiện không khả thi (`N/A`) và căn cứ. Tránh tổ hợp vô nghĩa.

## 3. State transition

Ví dụ workflow: `DRAFT -> REVIEW -> PUBLISHED`. Xác nhận các transition thật từ spec trước khi viết expected.

- Hợp lệ: `DRAFT -> REVIEW`, `REVIEW -> PUBLISHED` khi đủ điều kiện.
- Bị cấm: `DRAFT -> PUBLISHED` nếu workflow cấm bỏ bước review.
- Thử lặp cùng event, event out-of-order, concurrent transitions.
- Oracle quan sát cả state database, phát sự kiện, audit, version.

## 4. Multi-tenant / concurrency / side effect

Đặt bất biến trước test:

- Người dùng tenant A không đọc/sửa/xem thống kê tài nguyên tenant B.
- Một idempotency key trong cùng phạm vi chỉ tạo một side effect được quy định; khác phạm vi xử lý theo contract.
- Retry sau timeout không gây duplicate transaction; lỗi hạ tầng không được che thành thành công giả.
- Quyền thay đổi giữa request và commit phải được xử lý theo chính sách hiện hành.

Phân biệt `negative` (input/permission sai), `failure` (hệ thống phụ thuộc lỗi), `security` (phá vỡ trust boundary) để thiết kế oracle rõ ràng.

## 5. Điều kiện chất lượng của dữ liệu mock

| Thuộc tính | Cách kiểm tra |
|---|---|
| Synthetic | Không chứa thông tin thật, token/secret thật |
| Schema valid | Kiểu, enum, constraints, uniqueness, relationships |
| Deterministic | Seed/fixed clock, định danh được dựng lại |
| Isolated | Không cross-test leakage, cleanup rõ |
| Representative | Có positive, boundary, invalid, conflict |
| Failure-ready | Có stub cho timeout/429/5xx/disconnect, chỉ trong môi trường test |

## 6. Tham khảo

- ISTQB CTFL v4.0.1: https://istqb.org/certifications/certified-tester-foundation-level-ctfl-v4-0/
- OWASP WSTG: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/
- OWASP ASVS: https://asvs.owasp.org/
- OWASP AISVS (chỉ khi test AI systems): https://owasp.org/projects/artificial-intelligence-security-verification-standard-aisvs-docs
