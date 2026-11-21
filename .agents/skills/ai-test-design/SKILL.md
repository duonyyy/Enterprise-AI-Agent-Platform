---
name: ai-test-design
description: Design traceable, risk-based software test cases from requirements, user stories, API contracts, code changes, and bugs. Use for AI-assisted test case generation, edge cases, negative cases, equivalence partitions, boundary values, decision tables, state transitions, synthetic mock data, and regression checklists. Produces review-ready test specifications; does not implement automated test code.
---

# AI Test Design — Thiết kế kiểm thử dựa trên nghiệp vụ

## Mục tiêu và ranh giới

Biến yêu cầu và rủi ro **đã có căn cứ** thành bộ test case có expected result kiểm chứng được. Dùng khi được yêu cầu thiết kế test case, edge/negative case, mock data, checklist regression hoặc đánh giá độ bao phủ yêu cầu trước khi viết test.

- **Đầu vào:** user story/acceptance criteria, tài liệu nghiệp vụ, hợp đồng API/schema, bug report, diff hoặc mã nguồn và test hiện có.
- **Đầu ra:** test matrix truy vết, các test case ưu tiên theo rủi ro, kế hoạch dữ liệu giả lập và regression checklist.
- **Không thuộc phạm vi:** viết/đổi test code, tự sửa production code, khẳng định test đã chạy. Nếu yêu cầu thực tế là lập trình hoặc chạy test, nêu rõ phạm vi chưa được xử lý và kết thúc nhiệm vụ thiết kế; không tự kích hoạt quy trình khác.
- Bám cấu trúc dự án hiện có. Nếu chỉ được hỏi lập kế hoạch thì xuất Markdown trong câu trả lời; chỉ ghi file vào repo khi ngữ cảnh cho phép.

## Nguyên tắc bất biến

1. **Expected result phải độc lập với implementation.** Code hiện tại mô tả hành vi đang có, không tự động là nguồn chân lý của hành vi mong muốn.
2. **Traceability:** mỗi test case gắn với requirement/acceptance criterion/bug/risk cụ thể; không gán mã yêu cầu giả rồi trình bày như mã có thật.
3. **Không bịa chính sách nghiệp vụ.** Ghi rõ `CONFIRMED`, `ASSUMPTION`, `NEEDS_DECISION`; khi tài liệu mâu thuẫn, nêu hai cách hiểu và rủi ro.
4. **Ít nhưng có giá trị:** ưu tiên phát hiện lỗi, không tạo hàng chục test case trùng lặp cho đủ số lượng.
5. **Test data an toàn:** dùng synthetic data và định danh giả; không sao chép PII, secret, token, hoặc dữ liệu production vào fixture.
6. **Rủi ro chi phối mức sâu:** authn/authz, tiền, ghi dữ liệu, đa tenant, dữ liệu nhạy cảm và tính nhất quán cần kiểm thử kỹ hơn UI ít tác động.
7. **Không tự ý chạy hành động phá huỷ** trong production/staging dùng chung; các trường hợp lỗi hạ tầng phải dùng môi trường cách ly hoặc mock/fault injection có kiểm soát.

## Quy trình bắt buộc

### 1. Thu thập evidence và lập bản đồ phạm vi

- Xem yêu cầu, acceptance criteria, schema/API, thay đổi gần nhất, test hiện có, các module gọi tới/được gọi bởi phạm vi thay đổi.
- Trích xuất **actor, quyền, điều kiện trước, input, business rule, state, side effect, kết quả, lỗi, khả năng khôi phục**.
- Ghi nguồn thực tế bằng đường dẫn file + symbol/dòng khi có; với đặc tả bên ngoài, ghi tên và phiên bản.
- Tạo `Requirements & assumptions` với ID ổn định. Đánh dấu điều chưa rõ, không lặng lẽ tự xác nhận.
- Nếu thiếu yêu cầu quá quan trọng, ưu tiên lập bộ test tạm với `NEEDS_DECISION` hơn là bịa expected result.

### 2. Phân tích rủi ro trước khi thiết kế case

Xếp `P0` (critical, bảo mật, dữ liệu/tiền, tenant isolation, mất dịch vụ nghiêm trọng), `P1` (core business, lỗi thường gặp), `P2` (phụ trợ, hiếm hoặc tác động thấp). Đây là quy ước ưu tiên cho bộ test, **không phải** đánh giá severity đã được xác nhận.

Đánh giá tối thiểu: impact, likelihood, changed surface, external dependencies, observability, reversibility. Đánh dấu các bất biến (invariants) như: không vượt quyền, không ghi lặp, không mất dữ liệu khi retry, trạng thái được chuyển hợp lệ.

### 3. Chọn kỹ thuật kiểm thử phù hợp (không dùng máy móc mọi kỹ thuật)

| Điều kiện | Kỹ thuật | Câu hỏi cần giải quyết |
|---|---|---|
| Input có miền giá trị | Equivalence partitioning | Phân lớp input hợp lệ/bất hợp lệ thế nào? |
| Có min/max/TTL/quota/size | Boundary value analysis | Ngay trước/tại/ngay sau ranh giới? |
| Nhiều điều kiện nghiệp vụ | Decision table | Các tổ hợp điều kiện nào làm đổi kết quả? |
| Lifecycle/workflow | State transition | Chuyển trạng thái hợp lệ và bị cấm? |
| API/service với side effects | Fault injection, idempotency | Timeout, retry, duplicate, partial commit? |
| Phân quyền/đa tenant | Role-permission matrix | Cross-user/cross-tenant, thiếu role, scope sai? |
| Thuật toán/data transform | Properties/invariants | Tính chất nào phải luôn đúng trên nhiều input? |
| UI và hành vi người dùng | User journey / accessibility | Luồng người dùng và trạng thái lỗi có kiểm chứng? |

Bổ sung theo ngữ cảnh: Unicode/tiếng Việt, null/empty/whitespace, time zone/DST, clock skew, precision/overflow, pagination, sorting, optimistic lock, concurrent requests, dependency unavailable, API schema, malformed payload, error leakage. Với hệ AI/agent, xem thêm prompt injection, tool authorization, approval boundary, output validity, nondeterminism (nếu áp dụng).

Tham khảo cách lựa chọn và ví dụ tại [references/techniques.md](references/techniques.md).

### 4. Viết test case có test oracle mạnh

Mỗi case **phải có**:

- `ID`, tên có mục đích rõ, loại (`positive|negative|boundary|state|security|failure|regression`), `P0/P1/P2`.
- `Requirement/risk ID` và `source evidence` hoặc `NEEDS_DECISION`.
- Preconditions/setup, input/test data, các bước hoặc stimulus có thể thực hiện được.
- Expected response **và** expected state/side effects khi liên quan (DB, event, audit log, cache, message, trạng thái nghiệp vụ).
- Cleanup/isolation, test level gợi ý (`unit|integration|API|E2E|manual`), automation candidate.

**Không chấp nhận** các oracle mơ hồ: “hoạt động đúng”, “trả lỗi phù hợp”, “không bị crash” nếu thiếu hành vi cụ thể cần chứng minh.

Không giả định mọi thao tác bất hợp lệ trả 400; status/validation detail phải dựa vào contract hoặc được ghi `NEEDS_DECISION`.

### 5. Sinh dữ liệu mock và regression checklist

- Dùng data factory/fixtures có seed ổn định, hợp lệ với schema/foreign keys/unique constraints.
- Tách valid, invalid, boundary, unauthorized, duplicate, conflict, concurrency/failure; tránh các giá trị ngẫu nhiên không tái hiện được.
- Mô tả mock nào dành cho unit test, dependency nào cần **dịch vụ thật trong môi trường test** để xác thực integration.
- Với mỗi thay đổi, lập regression liên quan: feature trực tiếp, contract, callers, quyền, migration, sự kiện, backward compatibility và các lỗi từng xảy ra.
- Checklist phải có hành động + expected observable result, không chỉ liệt kê tên module.

### 6. Tự phản biện trước khi hoàn tất

Kiểm tra lần lượt: thiếu positive/negative/boundary? logic mâu thuẫn? case trùng? precondition khó tái tạo? expected result chưa có nguồn? thiếu tenant/authz/idempotency? test data vi phạm schema? test quá phụ thuộc implementation? thiếu cleanup? regression thiếu upstream/downstream?

Chấm `READY`, `REVIEW_REQUIRED`, `BLOCKED` cho từng case. Không chuyển một `NEEDS_DECISION` thành `READY` chỉ vì AI tự suy diễn.

## Đầu ra của skill

Khi được phép tạo tài liệu, ưu tiên tạo dưới `qa/` (hoặc vị trí chuẩn của repo):

- `test-design.md`: phạm vi, sources, assumptions, risk map, traceability matrix.
- `test-cases.md`: test case cụ thể theo mẫu.
- `mock-data-plan.md`: synthetic fixtures, generators, isolation, cleanup.
- `regression-checklist.md`: tập tối thiểu cần chạy cho thay đổi.

Nếu task nhỏ, có thể gộp các mục trong **một** tài liệu nhưng vẫn giữ đầy đủ các phần bắt buộc. Xem [references/output-template.md](references/output-template.md).

## Tiêu chí hoàn thành độc lập

- Hoàn thành test design khi các test case trong phạm vi yêu cầu đều có oracle có căn cứ hoặc được đánh dấu `NEEDS_DECISION`, được phân mức rủi ro và có dữ liệu/precondition đủ rõ để sử dụng.
- Tự kiểm tra traceability, sự trùng lặp, độ bao phủ các luồng lỗi quan trọng và tính an toàn của mock data; liệt kê các thiếu sót chưa thể giải quyết.
- Cuối câu trả lời tóm tắt số requirement đã nhận diện, số case theo P0/P1/P2, các quy tắc chưa xác nhận và rủi ro chưa được bao phủ. Không ghi “100% coverage” nếu không có cách đo và phạm vi đo.
- **Dừng tại đầu ra của chính skill này.** Không mặc định chuyển giao, yêu cầu gọi, hoặc coi bất kỳ skill khác là điều kiện hoàn thành.

## Nguồn tham khảo chính thức

Nguồn và cách sử dụng: [references/SOURCES.md](references/SOURCES.md). Tài liệu này thuộc riêng skill hiện tại.
