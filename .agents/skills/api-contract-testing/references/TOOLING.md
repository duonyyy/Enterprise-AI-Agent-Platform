# Công cụ và kỹ thuật triển khai

## Chọn phương thức
- **Spec-first:** validate OpenAPI YAML/JSON đúng version được repo khai báo, rồi validate các responses runtime với schema tương ứng.
- **Consumer-driven:** Pact consumer tests tạo interaction từ usage thực; provider verification phải chạy provider trong môi trường test có provider states rõ ràng.
- **Async:** validate message envelopes, type/version, unknown-field policy, headers, encoding; kiểm tra schema registry compatibility khi hệ thống dùng registry.
- **Breaking change:** không chỉ diff response field; xét header/content-type, status codes, pagination, enum, auth requirements, order/semantics.

## Ví dụ matrix mẫu
```yaml
operation: GET /v1/agents/{id}
contract_source: openapi.yaml
scenarios:
  - name: owned-agent
    principal: owner
    expected_status: 200
    validate_response_schema: true
  - name: unknown-agent
    principal: owner
    expected_status: 404
    validate_error_schema: true
  - name: foreign-agent
    principal: foreign_workspace
    expected_status: policy_defined # xác minh 403 hay 404
```

## Provider verification prerequisites
- Provider state tạo fixture đúng lúc và có thể reset độc lập.
- Pact verification không được chỉ xác nhận against consumer's mock.
- Với Broker, `can-i-deploy` chỉ hữu ích khi version/deployment và verification results được cập nhật chính xác.

## Cạm bẫy
OpenAPI **3.0** có khác biệt với OpenAPI **3.1+** về JSON Schema. Không dùng validator/dialect sai phiên bản; không suy luận `nullable` cùng quy tắc cho mọi version.
