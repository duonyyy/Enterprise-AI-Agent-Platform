---
name: api-contract-testing
description: "Kiểm thử hợp đồng giao tiếp API và sự tương thích consumer-provider. Dùng khi cần kiểm tra OpenAPI/JSON Schema, request/response, lỗi HTTP, backward compatibility, Pact consumer-driven contracts, webhook hoặc message schema RabbitMQ/Kafka; làm rõ contract source of truth, provider states và deployment compatibility."
metadata:
  language: "vi"
  version: "1.0.0"
---
# API Contract Testing — hợp đồng ổn định giữa các dịch vụ

## Kích hoạt và phạm vi
**Khi nào dùng:** Có yêu cầu test OpenAPI, JSON Schema, Pact, provider verification, compatibility, API versioning, schema evolution, webhook/event contract hoặc hành vi response khác tài liệu.

**Không dùng để:** Không thay E2E cho toàn bộ nghiệp vụ; không tự coi schema match là proof authorization, transaction correctness hoặc semantic correctness.

**Mục tiêu độc lập:** Phát hiện thay đổi làm hỏng consumer, khác biệt giữa tài liệu và runtime, và sai khác về định dạng thông điệp bằng kiểm chứng có phiên bản.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- OpenAPI/spec đã phê duyệt (kiểm tra version), hoặc consumer expectations làm nguồn sự thật; code implementation.
- Danh sách consumer/provider, route/topic/event, payload, headers, auth, semantics và các phiên bản đang sử dụng.
- Test base URL/stub/provider states, môi trường và quyền truy cập.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Xác định loại hợp đồng: provider schema (OpenAPI), consumer-driven Pact, async message schema, hoặc webhook; ghi nguồn sự thật và version rõ ràng.
2. Lập ma trận operation/message: input schema, response status/media type, error model, headers, required/nullable, enum, pagination và side effects được cam kết.
3. So sánh thay đổi baseline → candidate; phân biệt đổi có khả năng breaking theo usage thực tế với đổi mô tả thuần túy.
4. Tạo test provider runtime cho từng operation và representative error; validate response thực theo đúng dialect/schema được khai báo, không tự đổi OAS version.
5. Với consumer-driven contracts, viết consumer interaction có provider state rõ ràng và verify chống lại provider thật trong test environment; không chỉ chạy mock consumer.
6. Với message/webhook, kiểm tra envelope/version/schema, optional fields, unknown fields, headers, delivery duplicate/order khi hợp đồng quy định.
7. Đánh giá backward/forward compatibility theo consumer đang triển khai và policy versioning; nếu dùng Pact Broker, đối chiếu deployment compatibility khi đủ dữ liệu.
8. Báo cáo mismatch có request/response đã redact, version, owner, severity, workaround; chạy regression cho những consumer có ảnh hưởng.

## Nguyên tắc an toàn và tính đúng đắn
- Không bỏ qua lỗi schema bằng cách làm tất cả field optional; không thêm undocumented behavior để hợp thức hóa sai sót.
- Không kết luận backward compatible chỉ vì OpenAPI diff không báo lỗi: cần consumer usage, provider verification và các quy tắc ngữ nghĩa.
- Không gọi API làm thay đổi dữ liệu production; fixture và provider state phải cách ly.
- Không hardcode secret hoặc PII trong pact files/fixtures/recordings.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `contract-inventory.md`: provider, consumer, version, contract source.
- `tests/contracts/` hoặc test theo cấu trúc repo; fixtures/schema/sanitized pacts nếu phù hợp.
- `contract-compatibility-report.md`: breaking changes, verification matrix, deployment readiness.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Mỗi contract nằm trong scope có test runtime/provider verification hoặc ghi rõ BLOCKED.
- Có negative/error path và response media type/status, không chỉ 200.
- Ghi rõ baseline/candidate version, test invocation và compatibility policy.
- Không đánh dấu deploy-safe khi thiếu thông tin phiên bản consumer hiện hoạt.

## Mẫu lời gọi skill
> Hãy sử dụng `api-contract-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
