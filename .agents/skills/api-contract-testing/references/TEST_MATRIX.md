# Test matrix — api-contract-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `API-01` | Declared success | Mỗi operation với payload hợp lệ | Status/media type/body conform contract | P0 |
| `API-02` | Bad request | Thiếu required, sai type, enum ngoài range | Error response theo schema, không 500 | P0 |
| `API-03` | Auth errors | Thiếu hoặc thiếu quyền truy cập | 401/403 và payload theo documented policy | P0 |
| `API-04` | Optional vs nullable | Field optional, explicit null, empty string | Semantics đúng OAS/schema; không đồng nhất các trạng thái | P1 |
| `API-05` | Pagination | Page/cursor invalid, next token, empty results | Stable pagination contract, no dropped/duplicate IDs | P1 |
| `API-06` | Provider state Pact | Consumer contract with provider state fixtures | Provider verification runs against actual provider test server | P0 |
| `API-07` | Breaking response | Remove/rename/type change required field | Detect consumer incompatibility and block release per policy | P0 |
| `API-08` | Message envelope | Unknown field, schema version, event id | Compatibility policy for consumer observed; field rules respected | P0 |
| `API-09` | Webhook signature | Invalid signature or replay timestamp | Reject unauthorized event, no side effect | P0 |
| `API-10` | Version matrix | Candidate provider vs deployed consumers | Deploy allowed only when verified compatible or explicit override | P0 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
