# OBSERVABILITY, TRACING & AUDIT GUIDE

## 1. Distributed Tracing (OpenTelemetry)
Mỗi Run được gắn với một `trace_id` duy nhất xuyên suốt:
`api.request -> agent.run -> skill.load -> tool.authorize -> tool.execute (MCP) -> run.finish`.

## 2. WORM Audit Trail
Bảng `governance.audit_events` được bảo vệ bởi trigger cấm xóa sửa. Mọi dữ liệu nhạy cảm, token, PII đều được redact trước khi ghi vào log.
