# Validation — 9 Independent Agent Skills

Ngày đóng gói: 2026-11-17.

Đã kiểm tra bằng Python và PyYAML:
- 9 thư mục skill, 9 SKILL.md, mỗi skill có references riêng.
- YAML frontmatter hợp lệ; `name` khớp với tên thư mục, đúng regex và giới hạn ký tự; description không rỗng/không quá 1024 ký tự.
- Tất cả đường dẫn Markdown `references/...` trong SKILL.md tồn tại.
- Không tham chiếu trực tiếp tên của skill khác bên trong từng SKILL.md.
- Không có trùng lặp tên skill; từng SKILL.md dưới 500 dòng.
- ZIP được kiểm tra CRC / `testzip()` sau khi đóng gói.

**Giới hạn:** Không chạy `skills-ref validate` chính thức và không thực thi bất kỳ test nào trong hệ thống ứng dụng. Kiểm tra cú pháp không chứng minh việc dùng skill tạo ra test đúng nghiệp vụ.

| Skill | Lines | References | Description chars |
|---|---:|---:|---:|
| `ai-test-design` | 108 | 3 | 398 |
| `ai-test-automation-review` | 118 | 3 | 433 |
| `security-testing` | 63 | 3 | 314 |
| `database-consistency-testing` | 63 | 3 | 328 |
| `api-contract-testing` | 63 | 3 | 317 |
| `reliability-chaos-testing` | 63 | 3 | 337 |
| `performance-load-testing` | 63 | 3 | 332 |
| `ai-agent-evaluation` | 63 | 3 | 343 |
| `ui-accessibility-testing` | 63 | 3 | 325 |
