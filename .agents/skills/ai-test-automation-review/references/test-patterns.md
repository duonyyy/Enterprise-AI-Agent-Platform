# Test patterns — chỉ dùng khi tương ứng với stack dự án

Các đoạn sau là **mẫu minh hoạ**, phải khớp business rule, DTO, router, service và test runner thực tế. Không copy nguyên nếu repo khác cấu trúc.

## 1. Python / pytest — negative cases có oracle rõ

```python
import pytest

@pytest.mark.parametrize(
    "quantity",
    [0, -1, 101],  # giả định miền hợp lệ 1..100
)
def test_rejects_quantity_outside_allowed_range(service, repository, quantity):
    before = repository.count()
    with pytest.raises(InvalidQuantityError):
        service.create_order(quantity=quantity)
    assert repository.count() == before  # không tạo side effect
```

`InvalidQuantityError`, `service` và `repository` là placeholder, chỉ thay bằng interface thực. Nếu service trả error object thay vì raise, assert theo contract thật.

## 2. Python / Hypothesis — property-based khi phù hợp

```python
from hypothesis import given, strategies as st

@given(st.lists(st.integers(), max_size=100))
def test_sort_preserves_elements(values):
    assert sorted(sorted(values)) == sorted(values)
```

Ví dụ này chỉ chứng minh một tính chất của `sorted` (idempotence); khi áp dụng cho domain service cần định nghĩa property nghiệp vụ có giá trị thực và tránh chỉ test thư viện chuẩn.

## 3. Playwright — user-visible, web-first, isolated

```ts
import { test, expect } from '@playwright/test';

test('validation prevents invalid order submission', async ({ page }) => {
  await page.goto('/orders/new');
  await page.getByLabel('Quantity').fill('0');
  await page.getByRole('button', { name: 'Create order' }).click();
  await expect(page.getByText('Quantity must be at least 1')).toBeVisible();
});
```

Ở ví dụ này, strings/labels/status là **giả định**; chỉ dùng nếu UI thực có các text đó. Cần bổ sung API/DB-level negative test để xác minh không có order được tạo khi requirement đòi hỏi.

## 4. Chọn isolation đúng

| Level | Nên mock | Không nên mock |
|---|---|---|
| Unit | Network/external clock/repository port | Hàm nghiệp vụ đang test |
| Integration | Third-party external charge/email (khi không cần contract thật) | Database khi mục tiêu là kiểm tra SQL/RLS/transaction |
| API | Unauthorized external services | Middleware/authorization nếu đây là target kiểm thử |
| E2E | Service ngoài khó kiểm soát theo chính sách | Hành trình người dùng cốt lõi |

## 5. Execution report template

```text
Test scope: <feature / PR / commit>
Business contract: <spec and version>
Environment: <local/isolated CI, dependencies>
Command: <exact command>
Exit code: <actual result>
Results: passed=?, failed=?, skipped=?, xfailed=?, not_run=?
Coverage: <actual tool + scope + value OR 'not measured'>
Failures: <test IDs, root cause hypothesis, evidence>
Flakiness investigation: <reproductions performed OR 'not investigated'>
Gaps / blocked: <missing service/spec, impacted P0 risks>
Recommendation: READY_FOR_REVIEW | CHANGES_REQUIRED | BLOCKED
```

## 6. Official docs

- pytest parametrization: https://pytest.org/en/latest/how-to/parametrize.html
- pytest fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html
- Playwright best practices: https://playwright.dev/docs/best-practices
- Playwright API testing: https://playwright.dev/docs/api-testing
- Hypothesis: https://hypothesis.works/
