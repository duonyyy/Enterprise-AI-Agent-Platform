# Test matrix — performance-load-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `PERF-01` | Smoke | 1-2 VUs, short duration, data integrity | Script valid; correct responses; low background load | P0 |
| `PERF-02` | Baseline | Normal traffic distribution, stable env | p50/p95/p99, error rate, RPS reproducible | P0 |
| `PERF-03` | Peak load | Expected peak with auth and realistic payload mix | SLO and business success maintained or fail documented | P0 |
| `PERF-04` | Open arrival rate | Constant arrival at target RPS | Record dropped iterations and achieved rate | P0 |
| `PERF-05` | Spike | Short increase to approved peak and decrease | Recovery and error window quantified | P1 |
| `PERF-06` | Soak | Bounded sustained load for leak detection | No unbounded memory/queue growth within test duration | P1 |
| `PERF-07` | Write path | Mixed reads/writes with unique data | No idempotency violations/dirty fixture contamination | P0 |
| `PERF-08` | Bottleneck mapping | CPU/RAM/DB pool/queue/Redis tracked | Relate saturation and tail latency; distinguish generator bottleneck | P1 |
| `PERF-09` | Comparison | Baseline vs candidate same load/env | Regression delta with sample counts and caveats | P1 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
