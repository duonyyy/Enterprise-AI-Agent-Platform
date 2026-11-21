---
name: performance-load-testing
description: "Thiết kế, triển khai và phân tích thử nghiệm hiệu năng và tải cho HTTP/API/workers. Dùng khi cần load, stress, spike, soak, capacity planning, p95/p99 latency, throughput, error rate, k6 scenarios/thresholds, saturation, bottleneck và performance regression; yêu cầu mô hình tải, SLO, environment parity và giới hạn traffic an toàn."
metadata:
  language: "vi"
  version: "1.0.0"
---
# Performance & Load Testing — đo tải có ngữ cảnh

## Kích hoạt và phạm vi
**Khi nào dùng:** Yêu cầu đo RPS, latency p95/p99, benchmark, load/stress/soak/spike test, capacity target, k6, response-time regression hoặc kiểm chứng throughput.

**Không dùng để:** Không dùng cho tấn công từ chối dịch vụ, traffic không được phép, hoặc lấy kết quả laptop thành cam kết capacity production.

**Mục tiêu độc lập:** Định lượng hành vi hệ thống tại mức tải đại diện, phát hiện điểm nghẽn và đánh giá SLO với số đo có thể tái lập.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- Đặc tả workload: user journey, traffic mix, peak RPS/arrival rate, think time, request sizes, auth và dữ liệu.
- SLO/SLI từ nhóm sản phẩm hoặc ngưỡng thí nghiệm được đánh dấu giả định; compute/resources/database pool của môi trường test.
- Endpoint/worker/queue được phép kiểm thử, monitoring và giới hạn chi phí/tải.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Làm rõ mục tiêu: load ở mức kỳ vọng, stress tìm breaking point, spike phản ứng tăng đột ngột, soak tìm leak/decay; không gom tất cả thành một test.
2. Thiết lập metrics: HTTP request duration p50/p95/p99, fail rate, throughput, saturation (CPU/memory/DB pool), queue depth/lag và business success.
3. Thiết kế workload đại diện: tỷ lệ read/write, authentication, session reuse, realistic payload; tránh cache-only benchmark do dữ liệu lặp.
4. Chọn mô hình tải phù hợp: closed VUs hoặc open arrival rate; ghi arrival rate ≠ VUs ≠ throughput; tham chiếu [TOOLING](references/TOOLING.md).
5. Chạy smoke/baseline tải thấp trong môi trường test đã được phép, warm-up theo hệ thống; kiểm tra kịch bản và data setup đúng trước khi tăng tải.
6. Đặt thresholds có oracle/giới hạn an toàn; k6 `check()` chỉ assertion thống kê, **thresholds** mới ảnh hưởng exit status theo docs.
7. Thực thi lặp lại có cấu hình nguồn lực nhất quán, ghi kết quả và hệ thống giám sát; abort nếu quá budget, error rate hoặc gây ảnh hưởng ngoài phạm vi.
8. Phân tích latency distribution, error types, dropped iterations, utilization, DB contention/queue; so với baseline và nêu tính đại diện của môi trường.

## Nguyên tắc an toàn và tính đúng đắn
- Không mặc định benchmark production, không gửi lượng lớn traffic tới host bên ngoài; cần quyền và limit rõ ràng.
- Không tự bịa SLO; nếu chưa có chỉ trình bày ngưỡng đề xuất/chạy thử, không gọi là pass theo hợp đồng.
- Không tự suy luận “chịu được 100K users” từ VUs hoặc từ một run; phải mô hình hóa active users, arrival rate, concurrency và resource limits.
- Không che giấu errors/retries, dropped iterations, test-client bottlenecks hoặc cold cache/warm cache khác nhau.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `performance-workload.md`: user journeys, mix, SLO, environment, data.
- `perf/` với k6 script hoặc công cụ có sẵn, CLI reproducible, thresholds và safe defaults.
- `performance-report.md`: plots/metrics, utilization, p95/p99, error budget, bottlenecks, confidence and limits.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Workload, concurrency/arrival rate và SLO/baseline có nguồn hoặc được ghi là giả định.
- Mỗi test có kết quả PASS/FAIL/BLOCKED/NOT_RUN và bằng chứng với tool/version.
- Phân biệt lỗi SUT với lỗi script/generator và chỉ ra số lượng mẫu.
- Thí nghiệm nằm trong giới hạn tải và có cleanup dữ liệu phát sinh.

## Mẫu lời gọi skill
> Hãy sử dụng `performance-load-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
