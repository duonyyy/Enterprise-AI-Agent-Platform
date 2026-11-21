---
name: reliability-chaos-testing
description: "Thiết kế và thực thi thử nghiệm reliability, fault injection và chaos có kiểm soát cho distributed systems. Dùng khi cần kiểm tra timeout, retry, circuit breaker, failover, queue redelivery, crash/restart worker, Redis/Postgres outage, recovery, SLO và dữ liệu sau sự cố; yêu cầu blast radius, steady-state hypothesis, abort và rollback."
metadata:
  language: "vi"
  version: "1.0.0"
---
# Reliability & Chaos Testing — thử lỗi có kiểm soát

## Kích hoạt và phạm vi
**Khi nào dùng:** Yêu cầu chaos experiment, fault injection, retry/backoff test, failover, outage, restart, queue duplicate delivery, worker recovery, resilience hoặc disaster recovery drill.

**Không dùng để:** Không tự chạy chaos trên production, hệ thống shared hay bên thứ ba; không thay kiểm tra bảo mật và backup restore thực tế nếu chưa có môi trường.

**Mục tiêu độc lập:** Kiểm tra hệ thống tiếp tục giữ được invariant và hồi phục khi dependency hoặc thành phần bị lỗi, theo giả thuyết định lượng và giới hạn tác động.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- Service graph, dependencies (DB, cache, broker, tool/LLM), SLO/SLI, retry/timeouts, queue semantics.
- Môi trường cô lập hoặc phê duyệt thực nghiệm có tên rõ ràng, observability, khả năng abort/rollback và người chịu trách nhiệm.
- Invariants nghiệp vụ: no duplicate side effects, approval preserved, no stale session acceptance, no data loss.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Định nghĩa steady state với SLI đo được (success rate, latency, queue lag, business invariant), baseline và cửa sổ quan sát.
2. Viết giả thuyết: khi gây một lỗi cụ thể, hệ thống phải phản ứng gì trong khoảng thời gian đã thống nhất; không tự đặt SLO làm yêu cầu chính thức.
3. Chọn một failure mode một lần: latency, timeout, response lỗi, disconnect, worker kill, broker redelivery, DB failover; xác định dependency và blast radius.
4. Thiết kế experiment theo [TEST_MATRIX](references/TEST_MATRIX.md) với preflight, injection, observation, abort, restore và post-check.
5. Xác minh môi trường và quyền chạy trước khi fault injection; mặc định chỉ mô phỏng bằng mock/proxy/container trong sandbox hoặc staging.
6. Chạy baseline trước, inject fault trong khoảng giới hạn, ghi correlation IDs/traces/metrics, lập tức abort nếu vượt ngưỡng hoặc mất khả năng kiểm soát.
7. Kiểm tra correctness sau hồi phục: no ghost writes, no stuck runs, dedupe, eventual convergence có hạn, receipt/audit/approval không mất.
8. Khôi phục môi trường, chạy hậu kiểm, so sánh baseline và fault window, ghi phát hiện + reproduction + residual risk.

## Nguyên tắc an toàn và tính đúng đắn
- Mặc định KHÔNG tác động production. Muốn thực nghiệm production cần yêu cầu/ủy quyền cụ thể, plan được phê duyệt, guardrails và người on-call theo quy định tổ chức.
- Không kill service hay ngắt mạng khi không có rollback/abort rõ ràng hoặc rủi ro mất dữ liệu chưa được giải quyết.
- Phân biệt fault-injection assertions với tình huống không thể tái lập do hạ tầng thiếu.
- Không coi timeout, eventual consistency hay retry vô hạn là kết quả chấp nhận được: đối chiếu mục tiêu nghiệp vụ.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `reliability-experiment-plan.md`: steady state, hypothesis, blast radius, preflight, abort, rollback.
- `reliability-tests/`: deterministic fault-injection scripts/tests khi môi trường cho phép.
- `reliability-report.md`: SLI trước/trong/sau, recovery time thực đo, post-state invariants, limitations.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Có baseline, cách đo và expected recovery cho từng experiment.
- Có approval/scope phù hợp, kill switch/abort và cleanup thực thi được.
- Đã xác minh dữ liệu cuối/side effects sau phục hồi hoặc báo BLOCKED.
- Bằng chứng chứa timestamp, version, failure mode, trace/metric và giới hạn.

## Mẫu lời gọi skill
> Hãy sử dụng `reliability-chaos-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
