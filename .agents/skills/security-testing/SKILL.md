---
name: security-testing
description: "Thiết kế, triển khai và đánh giá kiểm thử bảo mật được ủy quyền cho ứng dụng, API và dịch vụ. Dùng khi cần kiểm tra authentication, OIDC/JWT, authorization, RBAC/ABAC, BOLA/IDOR, session, CSRF, tenant isolation, input validation, secrets và security regression; xuất bằng chứng, mức độ rủi ro và giới hạn kiểm thử."
metadata:
  language: "vi"
  version: "1.0.0"
---
# Security Testing — kiểm thử bảo mật có bằng chứng

## Kích hoạt và phạm vi
**Khi nào dùng:** Người dùng yêu cầu security test, penetration test trong phạm vi cho phép, kiểm tra OIDC/JWT/session, phân quyền, RLS/tenant isolation, API abuse, security regression hoặc review auth/security-sensitive code.

**Không dùng để:** Không thay audit pháp lý, chứng nhận compliance, hoặc thực hiện thử nghiệm xâm nhập vào hệ thống không có quyền kiểm thử.

**Mục tiêu độc lập:** Xác nhận các bất biến bảo mật trên ranh giới tin cậy bằng kiểm thử tái lập được; không nhầm quét tự động với chứng minh an toàn.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- Repo/source code hoặc endpoint + API contract; bản mô tả authn/authz, tài nguyên và roles.
- Phạm vi môi trường/tenant được phép, credentials **kiểm thử**, ngưỡng tải, yêu cầu nghiệp vụ và threat model (nếu có).
- Các yêu cầu bảo mật đã xác nhận, mức độ tác động và tiêu chí pass/fail; nếu thiếu thì lập bảng giả định cần xác nhận.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Lập inventory điểm vào (route, method, role, tài nguyên, token/cookie, side effect), luồng dữ liệu và ranh giới tin cậy. Ghi rõ đối tượng không nằm trong phạm vi.
2. Xác định security invariants/authorization matrix: ai được phép đọc/ghi tài nguyên nào trong tenant nào; kiểm tra cả object-level và function-level.
3. Dùng OWASP ASVS 5.0 + WSTG và API Security Top 10 làm tài liệu tham chiếu; chọn **kiểm soát có liên quan**, không gắn nhãn pass cho mục chưa kiểm chứng.
4. Thiết kế ca dương tính, từ chối, hết hạn, giả mạo, replay, revoke, thay đổi quyền, race, isolation, injection, failure of dependencies; xem [TEST_MATRIX](references/TEST_MATRIX.md).
5. Xây tài khoản/tenant giả và dữ liệu không nhạy cảm; dùng môi trường phát triển/staging có quyền. Kiểm tra xử lý cookie, claims, expiry, JWKS, OAuth state/PKCE ở các vị trí thích hợp.
6. Viết unit/integration/API tests tái lập; dùng client không có quyền và principal có quyền; thực thi kiểm thử thụ động trước khi cân nhắc kiểm thử chủ động được ủy quyền.
7. Kiểm tra **server-side** hiệu lực phân quyền, không chỉ status code; xác minh DB không thay đổi, token không hợp lệ, không lộ dữ liệu và audit event được ghi.
8. Ghi nhận các phát hiện theo đường dẫn bằng chứng, mức độ ảnh hưởng, điều kiện tái hiện, nguyên nhân khả dĩ và đề xuất sửa; chạy lại regression đúng phạm vi.

## Nguyên tắc an toàn và tính đúng đắn
- Chỉ kiểm thử hệ thống được sở hữu hoặc có ủy quyền rõ ràng. Không dò quét Internet, brute-force, DoS, hoặc khai thác phá hoại theo mặc định.
- Không in token/cookie/password/PII vào log, screenshot, report; luôn redact bằng chứng và không commit secrets.
- Không vô hiệu hóa authn, RLS, kiểm tra CSRF hoặc rate-limit để khiến test chạy qua.
- Không báo cáo “không có lỗ hổng” khi chỉ kiểm tra vài tuyến; ghi phần chưa kiểm thử và các giới hạn.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `security-test-matrix.md`: invariant → tình huống → quyền thử → oracle → ưu tiên.
- `security-tests/` hoặc test file phù hợp cấu trúc repo, tuyệt đối không gắn endpoint thật nếu chưa xác minh.
- `security-findings.md`: severity, evidence, reproduction, mitigation, regression; report phải tách PASS/FAIL/BLOCKED/NOT_RUN.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Mỗi security invariant trọng yếu có ít nhất một negative test có oracle.
- Cross-tenant, privilege escalation, revoke/expiry và hỏng dependency được xét nếu có liên quan.
- Có lệnh thực thi, môi trường, commit/version và bằng chứng hoặc ghi rõ lý do chưa chạy.
- Không để lại dữ liệu test có hại, secrets hay thay đổi môi trường chưa phục hồi.

## Mẫu lời gọi skill
> Hãy sử dụng `security-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
