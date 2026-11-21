# Công cụ và kỹ thuật triển khai

## Playwright + axe example
```typescript
import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';

test('settings: accessibility scan', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: /settings/i })).toBeVisible();
  const result = await new AxeBuilder({ page }).analyze();
  expect(result.violations).toEqual([]);
});
```
Gắn route, baseURL, data fixture vào repo; một test passing không chứng minh toàn bộ trang đạt WCAG.

## Manual test card
```
Page/state: Login → invalid credentials
Input method: keyboard only + screen reader where available
Steps: Tab through controls → submit invalid form → read error → retry
Expected: focus order follows task, error announced/associated with field,
          focus visible, retry possible without mouse
Evidence: browser/version, steps, screenshot redacted, human observation
```

## Visual regression
- Pin browser/font/viewport/device scale, locale/timezone, animation preferences.
- Chỉ cập nhật golden screenshot sau khi review thay đổi và xác nhận không mất chức năng.
- Screenshot không xác minh accessible name, screen reader announce hay keyboard flow.

## WCAG mapping
Gắn finding với *đúng* success criterion, mức A/AA nếu liên quan, và interaction state. Các vấn đề về semantic hoặc reading order cần người kiểm chứng.
