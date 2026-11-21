# Nguồn chính thức — AI Test Automation & QA Review

Tài liệu dùng để kiểm tra chất lượng implementation, test isolation và xác nhận kết quả kiểm thử. Không thay thế business requirements hoặc bằng chứng thực thi thực tế.

1. [ISTQB CTFL v4.0](https://istqb.org/certifications/certified-tester-foundation-level-ctfl-v4-0/) — test levels, test management, risk, test automation benefits and risks.
2. [pytest documentation](https://docs.pytest.org/en/stable/) — fixtures, parametrization, assertions, test configuration.
3. [Playwright Best Practices](https://playwright.dev/docs/best-practices) — assertions, user-visible behavior, test isolation.
4. [Playwright Test](https://playwright.dev/docs/intro) — chạy/đánh giá E2E test nếu repo dùng Playwright.
5. [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/) — security-oriented tests khi phù hợp và được cho phép.
6. [Agent Skills specification](https://agentskills.io/specification) — metadata và local reference links.

## Cách sử dụng nguồn
- Dùng framework thật được phát hiện trong repo, không tự động cài tất cả framework được liệt kê.
- Đảm bảo test isolation và expected results có nguồn, không viết assertion khớp với bug.
- Báo PASS chỉ khi có log thực thi; nếu thiếu dịch vụ, permission hoặc fixtures, dùng BLOCKED/NOT_RUN kèm lý do.
