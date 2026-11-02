# API DESIGN GUIDELINES & ERROR ENVELOPES

## 1. Chuẩn hóa REST API
- **Prefix:** `/api/v1/workspaces/{workspace_id}/...`
- **Idempotency:** Các endpoint POST thực thi nghiệp vụ hoặc kích hoạt Run bắt buộc yêu cầu header `Idempotency-Key: <UUID>`.
- **Phân trang:** Hỗ trợ con trỏ `limit` & `starting_after`.

## 2. Error Envelope Chuẩn (RFC 9457)
Mọi lỗi trả về từ API đều tuân thủ cấu trúc thống nhất:
```json
{
  "type": "https://errors.platform.internal/errors/RULE_VIOLATION",
  "title": "Policy Violation",
  "status": 403,
  "detail": "Action rejected by workspace safety policy",
  "instance": "/api/v1/workspaces/ws-1/agents/agent-1/runs",
  "code": "POLICY_REJECTED",
  "requestId": "req-12345"
}
```
