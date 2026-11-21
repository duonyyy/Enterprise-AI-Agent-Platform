# Test matrix — reliability-chaos-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `REL-01` | Redis timeout | Inject bounded cache timeout | Request degrades per policy; no wrong authz or session acceptance | P0 |
| `REL-02` | PostgreSQL outage | Suspend DB in isolated env temporarily | No false success on writes, recover and retry bounded | P0 |
| `REL-03` | Worker crash | Kill worker after processing before ack | Run state recoverable; no duplicated external side effects | P0 |
| `REL-04` | RabbitMQ redelivery | Ack fail then message delivered again | Idempotent handler, consistent receipts | P0 |
| `REL-05` | Slow tool | Tool latency beyond timeout budget | Timeout, backoff, bounded retries and accurate run status | P0 |
| `REL-06` | Partial downstream failure | One dependency 5xx while another succeeds | No unsupported success claim; consistent compensation | P0 |
| `REL-07` | Approval interrupted | Crash during WAITING_FOR_APPROVAL | State persisted, no unauthorized execution on replay | P0 |
| `REL-08` | Recovery measure | Remove fault, observe steady-state return | Recovery time measured, queues drain to accepted threshold | P1 |
| `REL-09` | Network partition | Temporary disconnect broker-client in lab | No silent data loss; retry/dedupe policy honored | P1 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
