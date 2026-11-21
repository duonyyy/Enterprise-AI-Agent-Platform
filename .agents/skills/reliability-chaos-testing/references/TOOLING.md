# Công cụ và kỹ thuật triển khai

## Thứ tự an toàn
1. Unit test với fake time/forced errors.
2. Integration test với Toxiproxy hoặc controlled mock service.
3. Container faults trong sandbox/staging có rollback và observability.
4. Kubernetes chaos nếu môi trường có policy/namespace selector/abort.
5. Production experiment **chỉ khi được ủy quyền cụ thể** và có quy trình vận hành riêng.

## Mẫu experiment
```yaml
experiment_id: rel-redis-timeout-001
environment: staging-isolated
hypothesis: >
  Khi Redis bị timeout 2 giây, API từ chối thao tác yêu cầu revocation
  thay vì báo thành công giả và phục hồi đúng khi Redis trở lại.
steady_state:
  slis: [http_error_rate, p95_latency, authorization_denials]
  baseline_window: 5m
fault:
  target: test-redis
  kind: network_latency
  duration: 30s
  max_delay: 2000ms
abort_if:
  - unexpected_cross_tenant_access
  - irreversible_data_change
rollback: remove_proxy_fault_and_check_redis_health
postcheck: verify_session_state_and_audit
```
Các giá trị ở trên là ví dụ thí nghiệm, **không phải ngưỡng SLO của dự án**.

## Xác minh hậu kiểm
- Trace/span liên kết request id, run id, business id với retries và queue events.
- Kiểm tra side effects và idempotency, không chỉ HTTP status.
- Ghi sự kiện abort/restore, thời gian phục hồi **đo được** và sai lệch so với baseline.
