# Template đầu ra — AI Test Design

> Thay giá trị minh hoạ theo bằng chứng dự án. Nếu không có căn cứ, ghi `NEEDS_DECISION`; không bịa số lượng hay kết quả.

## 1. Scope & sources

- Feature / PR / bug:
- In scope / out of scope:
- References (file path, requirement ID, API version):
- Existing test baseline:
- Critical invariants:
- Assumptions / open questions:

## 2. Requirement traceability & risk matrix

| Req ID | Rule / acceptance criterion | Evidence | Risk | Test IDs | Status |
|---|---|---|---|---|---|
| REQ-EXAMPLE-01 | [Nêu rule thực] | [File/line] | P0 | TC-001, TC-002 | CONFIRMED |

## 3. Test cases

### TC-001 — [Mục tiêu hành vi]

- **Type:** positive / negative / boundary / security / failure / state / regression
- **Priority:** P0 / P1 / P2
- **Requirement/risk:** [ID + evidence]
- **Level / automation:** API / integration / unit / E2E / manual; yes/no/conditional
- **Preconditions:** [Role, tenant, state, environment]
- **Test data:** [Fixture ID + trường đầu vào]
- **Steps/stimulus:** [Các thao tác hoặc request]
- **Expected response:** [Giá trị/status/schema theo contract]
- **Expected state/side effects:** [DB row/event/cache/audit/không thay đổi]
- **Cleanup:** [Rollback/fixture teardown]
- **Specification status:** READY / REVIEW_REQUIRED / BLOCKED

## 4. Mock data plan

| Fixture ID | Intent | Fields / constraints | Factory/seed | Isolation/cleanup |
|---|---|---|---|---|
| DATA-001 | Valid baseline | [field=value] | seed=123 | transaction rollback |

## 5. Regression checklist

- [ ] [Function/contract directly changed] — expected [observable condition]
- [ ] [Caller/consumer] — expected [observable condition]
- [ ] [Permission/security boundary] — expected [observable condition]
- [ ] [Related error/retry/failure path] — expected [observable condition]
- [ ] [Data migration/compatibility, if applicable] — expected [observable condition]

## 6. Self-review & completion summary

- Coverage summary: X confirmed requirements, Y mapped, Z not mapped (chỉ dùng số đếm thực tế).
- Distribution: P0 / P1 / P2.
- Duplicates removed / missing checks.
- `NEEDS_DECISION`: các câu hỏi cụ thể + ảnh hưởng nếu chưa trả lời.
- Explicitly untested risks.
- Completion: test design đã hoàn chỉnh trong phạm vi có căn cứ; các phần chưa thể xác định được ghi rõ, không giả định có bước chuyển giao sang công việc khác.
