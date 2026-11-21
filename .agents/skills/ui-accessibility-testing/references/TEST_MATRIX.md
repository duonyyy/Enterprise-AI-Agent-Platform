# Test matrix — ui-accessibility-testing

Các tình huống sau là **gợi ý kiểm thử**, không thay thế expected result của sản phẩm. Khi áp dụng, lập bảng chi tiết với các cột: `case_id`, `requirement/risk`, `setup/fixture`, `principal/role`, `steps`, `oracle`, `priority`, `result`, `evidence`, `cleanup`.

## Bộ tình huống trọng tâm

| ID | Rủi ro / nhánh test | Setup / thao tác | Oracle quan sát được | Ưu tiên |
|---|---|---|---|---|
| `UI-01` | Keyboard tab | Navigate primary journey with keyboard only | Logical focus order, visible focus, no trap | P0 |
| `UI-02` | Dialog lifecycle | Open/modal, close Escape, submit form | Initial focus, focus containment, focus return | P0 |
| `UI-03` | Form errors | Submit missing/invalid input | Error text, accessible association, focus/announcements | P0 |
| `UI-04` | Accessible names | Interactive controls and icons | Correct role/name/state exposed; no empty button name | P0 |
| `UI-05` | Live updates | Loading/error/SSE content | Appropriate non-disruptive status announcement | P1 |
| `UI-06` | Contrast | Text, UI boundaries and focus indicator | Measure against applicable WCAG criterion | P0 |
| `UI-07` | Reflow | Mobile viewport or zoom high | No loss of content/function or unintended two-axis scroll | P1 |
| `UI-08` | Axe scan | Each key page state after interaction | Report violations AND incomplete/manual tests | P0 |
| `UI-09` | Visual regression | Controlled screenshot environment | Reviewed intentional vs unintended differences | P1 |
| `UI-10` | Screen reader | Landmarks, tables, dynamic forms | Human/manual inspection for real spoken semantics | P0 |

## Quy tắc quyết định

1. Gắn từng test với yêu cầu nghiệp vụ hoặc invariant xác định được trong repo; nếu chưa có thì ghi `assumption` và yêu cầu người phụ trách xác nhận trước release gate.
2. Nêu rõ fixtures và trạng thái trước/sau; có assertions đủ mạnh để bắt lỗi, kể cả side effects bất thường.
3. Không đánh dấu pass bằng cách mock chính thứ đang cần xác minh; ví dụ không mock database khi đang tuyên bố xác minh RLS thực.
4. Khai báo test chạy được ở mức `unit/integration/system/manual`, chi phí, điều kiện dừng và rủi ro.
5. Khi fail: thu gọn case tái hiện được và kiểm tra false positives, tránh sửa test để hợp thức hóa bug.
