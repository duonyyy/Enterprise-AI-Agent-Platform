# Test matrix — security-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `SEC-01` | Unauthorized / invalid token | Không token, sai chữ ký, sai issuer/audience | 401/deny; không đọc/ghi dữ liệu; không trả diagnostic nhạy cảm | P0 |
| `SEC-02` | BOLA cross-tenant read | User A truy cập resource ID hợp lệ tenant B | Deny theo policy; response không chứa dữ liệu B | P0 |
| `SEC-03` | Cross-tenant write | User A PATCH/DELETE object B | Deny; row B và audit state đúng/không bị thay đổi | P0 |
| `SEC-04` | Role change | Đổi quyền user rồi dùng quyền cũ / scoped token | Quyền cũ bị từ chối theo cơ chế revocation/expiry đã định | P0 |
| `SEC-05` | Session revocation | Logout/force revoke, gọi lại bằng cookie/token cũ | Không thể tái sử dụng session sau commit revocation | P0 |
| `SEC-06` | Redis unavailable on logout | Inject Redis error trong server-side revoke | Không báo logout success giả; cookie xử lý theo thiết kế; có log | P0 |
| `SEC-07` | CSRF / Origin | Cross-site POST với cookie mà thiếu CSRF hoặc sai Origin | 403/deny; không có side effect | P0 |
| `SEC-08` | OIDC callback tamper | state/nonce/PKCE không khớp, expired callback | Từ chối callback; không tạo session | P0 |
| `SEC-09` | Input rejection | Payload vượt schema, injection-like metacharacters test-only | Validation safe; SQL parameterized; không lộ stacktrace | P1 |
| `SEC-10` | Rate-limit abuse | Vượt ngưỡng nhỏ trong staging có phép | Throttling đúng contract; không gây hại dịch vụ | P1 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
