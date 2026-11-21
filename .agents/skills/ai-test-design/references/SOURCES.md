# Nguồn chính thức — AI Test Design

Tài liệu dùng để chọn kỹ thuật và xác định cách đánh giá độ bao phủ. Đây là nguồn tham khảo, không thay thế acceptance criteria hay business rules của dự án.

1. [ISTQB CTFL v4.0](https://istqb.org/certifications/certified-tester-foundation-level-ctfl-v4-0/) — fundamentals, test analysis/design, risk-based testing, defect reporting.
2. [ISTQB Glossary](https://glossary.istqb.org/) — thuật ngữ test case, test basis, test condition, test oracle, coverage, boundary value.
3. [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/) — security requirements nếu thiết kế authn/authz/security cases.
4. [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/) — tư duy case security cho ứng dụng web/API.
5. [Agent Skills specification](https://agentskills.io/specification) — metadata, cấu trúc và local reference links.

## Cách sử dụng nguồn
- Đọc ISTQB trước khi chọn kỹ thuật; chỉ áp dụng kỹ thuật phù hợp với bề mặt chức năng.
- Dùng hợp đồng API, requirement, business rule và mã nguồn trong repo để xác định expected result; không lấy ví dụ chung làm oracle cho hệ thống cụ thể.
- Test cases mẫu cần điều chỉnh theo bằng chứng thật và ghi rõ assumption khi chưa xác nhận.
