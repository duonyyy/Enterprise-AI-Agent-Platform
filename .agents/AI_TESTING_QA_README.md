# AI Testing & QA — 9 Independent Agent Skills

Bộ 9 Agent Skills chuyên về software testing và quality assurance, viết bằng tiếng Việt, sử dụng cấu trúc của [Agent Skills specification](https://agentskills.io/specification). Hai skill thiết kế/tự động hóa kiểm thử được bổ sung bên cạnh bảy skill chuyên sâu.

**Tất cả các skill hoàn toàn độc lập.** Mỗi skill có `SKILL.md` cùng tài liệu `references/` của chính mình, tự đọc source/requirements cần thiết, có tiêu chí hoàn thành riêng. Không có pipeline, chuyển giao, auto chaining hay yêu cầu đầu ra của skill khác.

## Danh sách và khi nào dùng

| Skill | Công việc phù hợp | Không nên dùng làm gì |
| --- | --- | --- |
| `ai-test-design` | Phân tích requirements, test matrix, positive/negative/edge/boundary, synthetic mock data, regression checklist | Viết hoặc chạy automated test |
| `ai-test-automation-review` | Review quality của test case/test suite; implement/verify unit, integration, API, E2E tests | Mặc định sửa production code hay báo PASS nếu chưa chạy |
| `security-testing` | Authentication, authorization, OIDC/JWT, sessions, RBAC, tenant isolation, OWASP | Quét hệ thống production/đối tượng ngoài phạm vi cho phép |
| `database-consistency-testing` | Transactions, RLS, migrations, concurrency, idempotency, outbox, Saga | Chạy migration phá huỷ trên production |
| `api-contract-testing` | OpenAPI, JSON Schema, consumer/provider compatibility, messaging contracts | Thay đổi hợp đồng API chỉ để làm test xanh |
| `reliability-chaos-testing` | Fault injection, retry, recovery, dependency outage, controlled failure | Chủ động gây sự cố trên production không có phê duyệt |
| `performance-load-testing` | Load/stress/spike/soak, thresholds, p95/p99, k6 | Bắn traffic vào production/third-party tùy tiện |
| `ai-agent-evaluation` | Trace, tool policy, RAG evaluation, hallucination and prompt regression | Tự chấm bằng model answer làm gold standard |
| `ui-accessibility-testing` | WCAG 2.2, keyboard, responsive, visual regression, axe, Playwright | Đồng nhất tool-scan xanh với WCAG compliance hoàn chỉnh |

## Cài đặt cho Codex

Giải nén ZIP **trực tiếp vào thư mục gốc của repository**; file ZIP đã chứa cấu trúc:

```text
.agents/skills/
  ai-test-design/
    SKILL.md
    references/
  ai-test-automation-review/
    SKILL.md
    references/
  security-testing/
    SKILL.md
    references/
  database-consistency-testing/
    SKILL.md
    references/
  api-contract-testing/
    SKILL.md
    references/
  reliability-chaos-testing/
    SKILL.md
    references/
  performance-load-testing/
    SKILL.md
    references/
  ai-agent-evaluation/
    SKILL.md
    references/
  ui-accessibility-testing/
    SKILL.md
    references/
```

Đọc danh sách bằng `/skills` và gọi `$<skill-name>` tường minh trong Codex. Skill có thể được phát hiện tự động dựa trên `description`, nhưng người dùng có thể chỉ định skill khi muốn kiểm soát phạm vi. Xem [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills).

## Prompt mẫu (mỗi skill độc lập)

**Thiết kế test — không viết code:**

```text
$ai-test-design
Hãy phân tích requirements và API contract của module identity.
Tạo positive/negative/boundary test cases, source/expected results,
mock data và regression checklist. Chỉ làm test design, không sửa code.
```

**Review và tự động hóa test — không đòi test plan có sẵn:**

```text
$ai-test-automation-review
Đọc source, requirements và tests của module identity.
Chế độ: IMPLEMENT + VERIFY (nếu môi trường cho phép).
Tự chọn những tests có tác dụng phát hiện lỗi, bổ sung test với
framework hiện có, chạy các test liên quan và báo command + actual result.
Không sửa production code. Không yêu cầu test plan lập sẵn.
```

**Một skill chuyên biệt:**

```text
$database-consistency-testing
Kiểm tra idempotency và concurrent requests trong order Saga.
Chỉ chạy ở local/test DB được phép. Báo phạm vi, lệnh, kết quả quan sát
được, rủi ro và các trường hợp chưa kiểm chứng.
```

## Cách dùng hiệu quả

1. Chọn một mục tiêu rõ và giới hạn module/endpoint/PR; không gọi 9 skill cùng lúc.
2. Chỉ định mode/phạm vi tác động: review-only, viết test, hoặc thực thi test; theo mode thực tế được skill hỗ trợ.
3. Cho biết expected behavior từ requirements/contract thay vì coi code hiện tại luôn đúng.
4. Ưu tiên fixtures có thể tái hiện; không dùng production PII/secrets.
5. Với tải, chaos và security probing, giới hạn môi trường, thời lượng, điều kiện dừng và quyền được phép.
6. Yêu cầu báo `PASS / FAIL / BLOCKED / NOT_RUN`, lệnh đã chạy, môi trường và evidence. Không chấp nhận claim “100% quality” nếu không có phép đo.

## Hướng dẫn kiểm thử cho các bước triển khai (Steps 01–04)

Xem [playbook kiểm thử Bước 01–04](./AI_TESTING_QA_STEPS_01_TO_04.md) để chọn skill, thiết kế ca theo contract, dùng fixture có ownership và ghi bằng chứng cho lưu trữ, authentication, workspace/RBAC và Agent Lifecycle.

Playbook revision 2.0 gồm 32 ca thiết kế và regression RF04-01…09; mọi ca mặc định `NOT_RUN`. Các lệnh là ứng viên cần kiểm baseline/môi trường trước thực thi. Playbook không thay implementation runbook, approved task packet hoặc báo cáo nghiệm thu G1–G4; mỗi skill vẫn là assignment độc lập.

## Lưu ý

- `references/` không dùng chung giữa các skill. Mỗi skill đủ dữ liệu để bắt đầu từ nhiệm vụ của chính nó.
- Các tài liệu tham khảo dùng để chọn kỹ thuật; yêu cầu nghiệp vụ thực tế của repository quyết định test oracle.
- [Báo cáo kiểm tra gói skill](./AI_TESTING_QA_VALIDATION.md) chỉ xác nhận định dạng/đóng gói, không thay bằng chứng kiểm thử runtime của dự án. Kết quả dự án phải gắn với baseline và review riêng.
