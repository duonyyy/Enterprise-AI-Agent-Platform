---
name: ai-test-automation-review
description: Independently review AI-generated test scenarios against business requirements, then implement, run, and improve reliable automated unit, integration, API, contract, and E2E tests. Use for test quality assurance, assertion review, mock/fixture validation, regression automation, pytest, Vitest/Jest, Playwright, test scripts, and evidence-based test reports. Do not blindly trust AI-proposed tests or claim results without execution.
---

# AI Test Automation & QA Review — Phản biện và tự động hoá kiểm thử

## Mục tiêu và phạm vi

Đánh giá chất lượng test case (khi được cung cấp), xác thực expected result theo nghiệp vụ, và/hoặc hiện thực test tự động đáng tin cậy bằng toolchain của repo. Skill có thể khởi động chỉ từ requirements, mã nguồn hoặc bug report, không cần có test case lập sẵn. Chỉ tuyên bố chất lượng dựa trên evidence đã quan sát.

- **Đầu vào:** requirements/user stories, API contract/schema, mã nguồn, diff/PR/bug report, test hiện có hoặc danh sách test case bất kỳ (nếu được cung cấp), cấu hình test và CI.
- **Đầu ra:** danh sách case được chấp nhận/sửa/bỏ/thêm, test code nếu được yêu cầu, kết quả chạy thực, regression summary và risk còn mở.
- **Chế độ:** `REVIEW_ONLY` (đánh giá test cases/test suite hoặc lập nhận xét QA); `IMPLEMENT` (tự xác định test cần thiết, viết/cải thiện test code); `VERIFY` (kiểm tra và chạy test/report). Chỉ chọn hành động thực sự thuộc yêu cầu; không tự ghi code nếu người dùng chỉ yêu cầu đánh giá.
- Không tự thay đổi code production để làm cho test xanh, trừ khi người dùng giao nhiệm vụ sửa bug; kể cả khi được sửa, giữ riêng bước xác nhận bug và thay đổi.

## Hard rules

1. Không coi test do AI viết là đúng chỉ vì test pass; **kiểm tra oracle với yêu cầu nghiệp vụ** trước khi thực thi.
2. Không tự ý sửa/xoá/skip/xfail test đang fail, làm yếu assertion, tăng timeout tuỳ tiện hoặc mock chức năng cốt lõi để đạt màu xanh.
3. Test phải độc lập, deterministic khi có thể, không cần thứ tự chạy cố định; không sử dụng sleep cứng thay cho đồng bộ điều kiện.
4. Không dùng tài khoản/dữ liệu thật, secrets, production endpoints hoặc gọi side-effect bên ngoài khi chưa được cho phép. Ưu tiên isolated test environment, disposable DB, fixture, fake clock, deterministic seed.
5. Phân biệt rõ `PASSED`, `FAILED`, `SKIPPED`, `BLOCKED`, `NOT_RUN`. Không giả lập command output, coverage, traces hoặc CI status.
6. Ưu tiên test public contract / observed behavior; implementation details chỉ là điểm kiểm thử khi đó chính là invariant kỹ thuật cần bảo vệ.
7. Không tự ý thêm framework/dependency, thay config CI, migration, hoặc chạy load/destructive test nếu nhiệm vụ không yêu cầu; đề xuất trước khi thực hiện thay đổi phạm vi lớn.

## Workflow

### Gate 0 — Xác minh điều kiện đầu vào

- Đọc acceptance criteria và version spec, cấu trúc repo, test scripts hiện có, `pyproject.toml` / `package.json` / config tương ứng.
- Nếu có case list từ AI: coi là **các giả thuyết cần phản biện**, không phải approved spec.
- Nếu không có case list: tự xác định expected behavior và test inventory tối thiểu từ requirements, contract, bug hoặc diff **ngay trong skill này**. Không đòi hỏi tài liệu hay đầu ra của bất kỳ skill nào khác.
- Khảo sát dependencies: DB, cache, queue, auth, external APIs, async workers; phân loại mock/stub vs thực chạy trong môi trường test.

### Gate 1 — Xác thực test oracle và độ bao phủ (áp dụng cho cả test mới)

Với test case được cung cấp **hoặc** test case được xác định trực tiếp từ requirements, kiểm tra:

| Dimension | Câu hỏi reviewer phải trả lời |
|---|---|
| Business fit | Có đúng rule/actor/permission/transition được xác nhận? |
| Oracle | Có assert cụ thể cho success, failure và side effects? |
| Risk | Có test đúng lỗi có hậu quả cao? |
| Traceability | Có link requirement/bug/contract và phạm vi? |
| Independence | Có chạy được riêng lẻ, data/clock/environment kiểm soát? |
| Feasibility | Có setup, fixture, cleanup, tầng test phù hợp? |
| Redundancy | Có trùng giá trị kiểm thử mà không tăng signal? |
| Mutation sensitivity | Nếu logic bị đảo/sai đi, test có fail không? |

Ra quyết định `KEEP`, `REWRITE`, `MERGE`, `ADD`, `DROP`, `BLOCKED` với lý do, evidence và mức ưu tiên. Những yêu cầu thiếu mô tả expected result phải là `BLOCKED`/`NEEDS_DECISION`, không “sáng tạo” nghiệp vụ.

Xem [references/review-rubric.md](references/review-rubric.md) trước khi chốt review.

### Gate 2 — Lập chiến lược automation

Chọn đúng tầng để tối ưu tốc độ và mức tin cậy:

- **Unit:** pure logic, validation, state reducers, domain services; mock only external boundaries; pytest / Vitest / Jest theo chuẩn có sẵn.
- **Integration:** DB transactions, RLS, cache, queue, workflow execution, migration, HTTP -> persistence -> side effects; dùng service thật trong environment cách ly khi integration chính là điều cần chứng minh.
- **API/contract:** status, schema, authn/authz, error codes, idempotency; provider/consumer contract nếu repo đã áp dụng.
- **E2E:** chỉ số hành trình critical có giá trị mà unit/integration không kiểm chứng; Playwright user-visible locators, web-first assertions, isolated context.
- **Property-based:** bất biến/tính chất dữ liệu có miền rộng (Python Hypothesis nếu đã sẵn có hoặc được cho phép bổ sung).

Không bắt buộc tỷ lệ cứng cho test pyramid; chọn theo lỗi cần phát hiện, cost và feedback speed.

### Gate 3 — Hiện thực có thể tái chạy

- Tuân thủ cấu trúc `tests/`, naming, fixtures, config và style của dự án; giữ test name nói rõ behavior.
- Mỗi test có một mục tiêu kiểm chứng chính; assert trạng thái trả về **và** side effects khi có.
- Với negative/security: chứng minh **không** phát sinh dữ liệu, sự kiện, token, quyền hoặc leak ngoài ý muốn.
- Với mock: đảm bảo mock ở đúng trust boundary, không mock đối tượng đang được test. Đừng giả lập DB khi cần kiểm tra transaction/RLS thực.
- Dùng fixtures/factories, test clock, cleanup và unique IDs cho parallel runs; xử lý eventual consistency bằng polling có deadline/condition thay vì sleep cứng.
- Ưu tiên parametrization cho một nghiệp vụ với nhiều bộ dữ liệu; tách case khi failure reason/behavior khác đáng kể.
- Có ít nhất một test chứng minh đường lỗi quan trọng nếu feature có failure semantics.

Ví dụ tối thiểu và mẫu assertions ở [references/test-patterns.md](references/test-patterns.md).

### Gate 4 — Execute & verify (nếu chế độ cho phép)

1. Chạy tập **nhỏ nhất liên quan** trước, dùng lệnh chuẩn repo; ghi command, environment, exit code.
2. Phân biệt lỗi product, lỗi test, lỗi environment hoặc thiếu spec. Không mặc định code sai chỉ vì test fail.
3. Sửa lỗi test/fixture trong phạm vi cho phép; không nới assertion để che lỗi ứng dụng. Sau chỉnh sửa, chạy lại case liên quan.
4. Chạy thêm regression chịu ảnh hưởng; lặp test độc lập / random order khi nghi ngờ flakiness (nếu hạ tầng cho phép).
5. Đọc coverage/branch/mutation report **chỉ nếu thực sự tạo được**. Coverage cao không thay thế assertion đúng.
6. Kiểm tra có test nào pass khi bỏ qua invariant không (mutation/sanity check hợp lý và an toàn trong môi trường test); không sửa production code chỉ để thử mutation trừ khi có cơ chế test riêng.
7. Nếu tool/framework/service không sẵn: đánh dấu `BLOCKED` và nêu cách chạy; không chuyển thành `PASSED`.

### Gate 5 — Security & AI-specific checks theo risk

- Với ứng dụng web/API: kiểm tra authorization (IDOR/cross-tenant), session/revocation, rate limits theo spec, input validation, sensitive error/logging, CSRF/CORS khi có ý nghĩa. Dùng OWASP ASVS/WSTG làm reference, không coi checklist chung là đủ cho mọi app.
- Với AI agent/LLM: khi **trong phạm vi** mới xét prompt injection, tool access policy, approval-gated side effect, output/schema validation, context/data isolation, replay, audit evidence, budget/time limits. Có thể đối chiếu OWASP AISVS theo mức rủi ro.
- Với async/saga/distributed: retry, duplicate event, idempotency, out-of-order message, compensation, crash recovery, timeout và dead-letter handling khi feature có các cơ chế này.

### Gate 6 — Báo cáo và QA gate

Giao `qa/test-review-report.md` (hoặc phản hồi Markdown khi review-only) gồm:

1. **Scope & ground truth:** sources/commit/diff, rule đã rõ và chưa rõ.
2. **Case review:** KEEP/REWRITE/MERGE/ADD/DROP/BLOCKED, lý do có evidence.
3. **Changes:** files created/changed, test ID liên kết requirement ID.
4. **Execution:** chính xác lệnh, exit codes, passed/failed/skipped/not run, dependency blockers.
5. **Regression & uncovered risks:** những phần đã/ chưa chạy, lý do.
6. **Recommendation:** `READY_FOR_REVIEW`, `CHANGES_REQUIRED` hoặc `BLOCKED` (đánh giá của reviewer, không tự nhận đã được doanh nghiệp phê duyệt).

**Không được xuất `READY_FOR_REVIEW` khi có P0 chưa kiểm chứng/đang fail, chưa được giải quyết hoặc thiếu spec ảnh hưởng an toàn, trừ khi được đánh dấu rõ là rủi ro được chấp nhận bởi người có thẩm quyền.**

## Tiêu chí hoàn thành độc lập

- `REVIEW_ONLY`: đưa ra quyết định KEEP/REWRITE/MERGE/ADD/DROP/BLOCKED đối với các case đang được review, với evidence và lý do; không khẳng định test đã chạy.
- `IMPLEMENT`: tạo hoặc cải thiện test code theo yêu cầu và chuẩn repo, chỉ rõ file thay đổi, giới hạn kiểm chứng và cách thực thi; không tự nhận `PASSED` nếu chưa chạy.
- `VERIFY`: chạy các test trong phạm vi cho phép, ghi kết quả thực, điều kiện môi trường và các rủi ro chưa kiểm chứng; phân biệt kết luận của reviewer với phê duyệt release.
- Tóm tắt nguồn nghiệp vụ, những case đã xác thực hoặc bổ sung, những file test đã thay đổi, bằng chứng thực thi và vấn đề còn mở. Nếu phát hiện lỗ hổng yêu cầu, **ghi lại thành open question/issue trong đầu ra hiện tại**; không yêu cầu kích hoạt skill khác.
- **Dừng tại đầu ra của chính skill này.** Không phụ thuộc test plan do skill khác tạo, không tự động giao việc hoặc gọi một skill khác.

## Nguồn tham khảo chính thức

Nguồn và cách sử dụng: [references/SOURCES.md](references/SOURCES.md). Tài liệu này thuộc riêng skill hiện tại.
