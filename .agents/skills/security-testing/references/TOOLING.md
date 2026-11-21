# Công cụ và kỹ thuật triển khai

## Chọn đúng tầng test
- **Unit:** decode/validate claims với thư viện vetted, authorization predicate, TTL/revocation policy và origin matcher; fake clock để ổn định thời gian.
- **Integration:** test HTTP + Redis/PostgreSQL thực trong Docker, ký JWT test keys và test OIDC fake IdP. Phải kiểm tra side effects.
- **API security:** xây matrix `principal × tenant × resource × action`; xác minh cả allow lẫn deny, role updates và tenant switching.
- **Thủ công có phép:** review config cookies Secure/HttpOnly/SameSite, TLS enforcement, JWKS rotation, secret handling, open redirects.

## Ví dụ pytest (minh họa — gắn vào fixture repo)
```python
import pytest

@pytest.mark.parametrize("principal,resource,expected", [
    ("tenant_a_viewer", "tenant_a_doc", 200),
    ("tenant_a_viewer", "tenant_b_doc", 403),  # có thể là 404 theo policy
    ("anonymous", "tenant_a_doc", 401),
])
async def test_document_access(principal, resource, expected, api_client, token_for, doc_id_for):
    headers = {"Authorization": f"Bearer {token_for(principal)}"} if principal != "anonymous" else {}
    response = await api_client.get(f"/v1/documents/{doc_id_for(resource)}", headers=headers)
    assert response.status_code == expected
    if expected != 200:
        assert "tenant_b_secret" not in response.text
```
**Lưu ý:** chỉ là skeleton; expected 403/404 do API policy quyết định. Cần test DB state trước/sau cho write, không chỉ status.

## Bằng chứng và triage
- Ghi `case_id`, branch/commit, principal/role, tenant **giả**, resource, expected, actual, status.
- Severity = impact × khả năng khai thác trong ngữ cảnh. Phân biệt confirmed exploit, suspicious, blocked, false positive.
- Không đưa payload nhạy cảm, token, mật khẩu vào report. Thử nghiệm tấn công chỉ trong scope được cấp phép.
