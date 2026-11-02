# TEST STRATEGY & QUALITY GATES

Hệ thống tuân thủ kim tự tháp kiểm thử nghiêm ngặt:
1. **Contract Tests (`tests/contract/`):** Xác minh tính tương thích 2 chiều giữa JSON Schema và Pydantic V2 models.
2. **Security & RLS Tests (`tests/security/`):** Kiểm thử cô lập đa tenant trên non-bypass roles; kiểm tra phòng vệ SSRF cho MCP và Zip-Slip cho Skill.
3. **Recovery Tests (`tests/recovery/`):** Giả lập dừng đột ngột tiến trình và xác minh khôi phục trạng thái từ PostgreSQL checkpoint.
4. **End-to-End Tests (`tests/e2e/`):** Chạy luồng hoàn chỉnh từ khởi tạo Agent, publish immutable version đến thực thi qua 12-Step ToolDispatcher.
