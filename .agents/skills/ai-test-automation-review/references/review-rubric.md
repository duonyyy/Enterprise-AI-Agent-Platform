# Review rubric — QA cho test case do AI đề xuất

## Review pipeline

1. **Business / evidence:** case có expected result được nguồn chứng minh? Nếu không -> `BLOCKED`.
2. **Fault-detection value:** case có phân biệt implementation đúng/sai? Nếu thiếu assert thực chất -> `REWRITE`.
3. **Risk coverage:** có che được bug nghiêm trọng không? Nếu thiếu P0 -> `ADD`.
4. **Isolation:** không phụ thuộc thứ tự test và dữ liệu ngẫu nhiên khó tái tạo? Nếu phụ thuộc -> `REWRITE`.
5. **Efficiency:** trùng nghĩa với case mạnh hơn? -> `MERGE`/`DROP` khi được giải trình.
6. **Automation readiness:** đã có setup, test data, observable result, cleanup, framework phù hợp?

## Decision table

| Decision | Điều kiện chọn | Hành động |
|---|---|---|
| KEEP | Correct, risk-relevant, testable | Map tới implementation/execution |
| REWRITE | Đúng ý nhưng sai oracle/fixture/granularity | Viết lại oracle/setup/assertions |
| MERGE | Cùng mục đích, dữ liệu có thể parametrize | Gộp nhưng giữ traceability |
| ADD | Missing boundary/security/negative/regression risk | Bổ sung test case |
| DROP | Out of scope/invalid/redundant | Ghi lý do và nguồn |
| BLOCKED | Business rule mâu thuẫn hoặc thiếu nguồn | Mở quyết định cần xác nhận |

## Ví dụ phản biện

**AI case:** “Gọi logout lúc Redis unavailable, expected 200 và cookie bị xoá.”

**Phản biện:** Nếu tài liệu yêu cầu server-side revocation phải thành công, expected 200 có thể sai nghiêm trọng. Tách xác minh: cookie-clearing behavior, HTTP contract khi infrastructure failure, session revocation state, audit/metrics. Nếu đặc tả chưa nêu cách xử lý HTTP thì đánh dấu `NEEDS_DECISION`, không gán status tuỳ ý. Đường lỗi phải bảo đảm không báo logout thành công giả.

## Template QA review

| Test ID | Review | Why | Evidence | Corrected oracle / test type | Risk |
|---|---|---|---|---|---|
| TC-001 | REWRITE | Assert chỉ kiểm tra 2xx | contract [path] | Assert response + DB unchanged on failure | P0 |

## Quality gates gợi ý

- P0 yêu cầu có case và kết quả kiểm chứng hoặc block được nêu rõ.
- Không có case `READY` với expected result suy luận từ code sai/ngược spec.
- Không có fixture chứa secret thật hoặc side effects đến production.
- Mọi outcome được hỗ trợ bằng lệnh chạy/test report; nếu chưa chạy, ghi `NOT_RUN`.
- Có reviewer riêng cho nghiệp vụ rủi ro cao khi chính sách dự án yêu cầu phê duyệt.

## Tài liệu

- ISTQB CTFL: https://istqb.org/certifications/certified-tester-foundation-level-ctfl-v4-0/
- OWASP ASVS 5.0: https://asvs.owasp.org/
- OWASP WSTG: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/
- OWASP AISVS: https://owasp.org/projects/artificial-intelligence-security-verification-standard-aisvs-docs
