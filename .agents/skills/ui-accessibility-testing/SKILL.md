---
name: ui-accessibility-testing
description: "Kiểm thử giao diện web về accessibility và tính đúng đắn tương tác. Dùng khi cần WCAG 2.2 AA audit, keyboard navigation, focus management, screen-reader semantics, accessible names, contrast, responsive/mobile, Playwright axe-core scans, browser compatibility và visual regression; kết hợp kiểm tra tự động với manual review."
metadata:
  language: "vi"
  version: "1.0.0"
---
# UI Accessibility Testing — giao diện sử dụng được và kiểm chứng được

## Kích hoạt và phạm vi
**Khi nào dùng:** Yêu cầu kiểm tra WCAG, accessibility, keyboard, screen reader, UI responsive, Playwright accessibility tests, axe-core, contrast, error messages, focus traps hoặc visual regression.

**Không dùng để:** Không thể thay đánh giá chuyên gia/người dùng khuyết tật; không biến snapshot pixel thành chứng minh usability hoặc accessibility.

**Mục tiêu độc lập:** Kiểm tra chức năng giao diện qua các cách tương tác và môi trường khác nhau, đặc biệt các yêu cầu về accessibility; không coi axe scan sạch là chuẩn WCAG hoàn chỉnh.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- UI routes, use cases, supported browsers/viewports, design tokens, target WCAG conformance level.
- Accessible test accounts + seed data, trạng thái UI quan trọng: empty/loading/error/form/dialog/menu.
- UI source code và môi trường chạy browser test có thể cô lập.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Xác định scope page/state và loại user journey; đặt baseline WCAG 2.2 A/AA theo yêu cầu, không tự tuyên bố tuân thủ toàn bộ.
2. Lập accessibility tree/semantics map: headings, landmarks, accessible names, roles, labels, table headers, live regions, validation errors.
3. Kiểm tra keyboard-only: tab order, skip links, visible focus, dialog open/close/focus return, dropdown, error focus và không kẹt bàn phím.
4. Dùng axe-core qua Playwright cho các vi phạm phát hiện tự động; kiểm tra cả trạng thái sau tương tác, không chỉ page load.
5. Đánh giá thủ công các điểm mà tool không tự xác định: logic reading order, alt-text quality, screen reader announcements, instructions và actual task completion.
6. Kiểm tra responsive ở viewport đại diện (desktop/tablet/mobile), zoom/reflow, text resize, orientation khi liên quan; tránh chỉ so pixel.
7. Visual regression với screenshot ổn định (fonts, animations, timezone, data), review diffs có chủ đích; không auto-accept snapshots thiếu đối chiếu.
8. Ghi finding theo WCAG success criterion (nếu áp dụng), user impact, keyboard steps, browser, evidence, suggested fix; retest sau sửa.

## Nguyên tắc an toàn và tính đúng đắn
- Công cụ scan tự động không chứng minh đạt WCAG; luôn cần manual assessment. W3C nêu rõ không công cụ nào đánh giá đầy đủ.
- Không thêm aria-label tùy tiện khi native HTML semantics đã đúng; ưu tiên sửa markup/label thực.
- Không coi màu sắc, visual snapshots hoặc score của một tool là thước đo duy nhất cho trải nghiệm người dùng.
- Không ghi PII/test credentials vào screenshots, HTML snapshots hoặc CI artifacts.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `accessibility-test-matrix.md`: route/state → WCAG → automated/manual check.
- `e2e/accessibility/` với Playwright + axe nếu dự án có toolchain phù hợp.
- `a11y-report.md`: violations, manual findings, screenshots redacted, browser/version, remediation.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Page states quan trọng được scan và keyboard-tested; phần thủ công được ghi đủ hoặc BLOCKED.
- Finding liên kết WCAG success criterion phù hợp, không suy luận sai chuẩn.
- Sự khác nhau giữa pixel regression và semantic regression được phân biệt.
- Kết luận không phóng đại thành chứng nhận WCAG toàn hệ thống.

## Mẫu lời gọi skill
> Hãy sử dụng `ui-accessibility-testing` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
