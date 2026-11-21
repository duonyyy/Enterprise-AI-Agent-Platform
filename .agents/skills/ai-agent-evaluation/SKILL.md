---
name: ai-agent-evaluation
description: "Đánh giá định lượng AI agents và RAG từ đầu đến cuối. Dùng khi cần xây evaluation dataset, gold labels, deterministic graders, LLM-as-judge rubric, trace grading, tool-call correctness, retrieval faithfulness, prompt/model regression, approval/permission, latency/cost và agent adversarial tests; chống leakage, báo confidence và human review."
metadata:
  language: "vi"
  version: "1.0.0"
---
# AI Agent Evaluation — kiểm chứng hành vi và chất lượng agent

## Kích hoạt và phạm vi
**Khi nào dùng:** Yêu cầu eval agent/RAG, trace scoring, prompt regression, model upgrade comparison, tool routing evaluation, hallucination/faithfulness, human-in-the-loop, SQL agent correctness hoặc evaluation dataset.

**Không dùng để:** Không dùng vài câu hỏi demo để kết luận agent production-ready; không tạo benchmark từ chính output cần đánh giá rồi báo pass.

**Mục tiêu độc lập:** Định nghĩa chất lượng bằng tập dữ liệu và tiêu chí có thể tái lập, đo cả answer, tool-usage, workflow governance và hiệu năng; không đồng nhất điểm LLM judge với chân lý.

Skill này **độc lập hoàn toàn**: tự đọc source/repo, xác định yêu cầu, thiết kế và kiểm tra trong phạm vi được giao. Không đòi hỏi artifact, kết quả hay kích hoạt skill khác. Nếu thiếu dữ liệu, nêu giả định và chỉ thực hiện phần an toàn có bằng chứng.

## Đầu vào cần xác định
- Use cases/capabilities, workflow graph, tool policy, prompts, model/version/temperature, grounding docs và approval rules.
- Đầu vào thực tế đã khử PII hoặc dữ liệu synthetic, gold reference hoặc chuyên gia chấm; ngưỡng chất lượng được duyệt.
- Trace/telemetry và ngân sách eval; có thể chạy với mock tools cho side effects nguy hiểm.

Nếu thông tin cốt lõi còn thiếu, phân loại `ASSUMPTION` / `UNVERIFIED` / `BLOCKED`, không tự bịa kỳ vọng hoặc tuyên bố test đã chạy.

## Quy trình thực thi
1. Viết success contract theo lớp: task completion, factuality/grounding, schema compliance, correct tool routing, authorization, approval, side effects, latency, tokens/cost.
2. Thiết kế dataset phân tầng: normal, ambiguous, out-of-scope, missing context, conflicting docs, adversarial prompt injection, tool failure, permission denied, approval reject.
3. Tách development set và holdout/test set theo nguồn/thời gian; khử PII, kiểm tra leakage/duplicate và ghi version dataset.
4. Định nghĩa metric và oracle: deterministic assertions cho tool args, state transitions, permission and schema; evidence-based judge cho nội dung; human review các nhãn khó.
5. Đo retrieval riêng với generation: recall/precision nếu có labels, context precision/faithfulness, source citation validity; không gộp mọi lỗi thành hallucination.
6. Chạy eval với cấu hình cố định và nhiều lần khi nondeterminism có thể ảnh hưởng; lưu seed nếu nền tảng hỗ trợ, model id, prompt hash và trace IDs.
7. Dùng trace-level grading để kiểm tra khi nào agent gọi tool, thứ tự, retries, approval, failures; đánh giá side effects thật bằng mock/sandbox, không qua câu trả lời tự báo.
8. So sánh baseline/candidate theo nhóm lỗi, rate, interval/variance khi đủ mẫu, quality vs cost/latency; tạo regression cases cho lỗi mới và báo chưa xác định.

## Nguyên tắc an toàn và tính đúng đắn
- Không đưa thông tin nội bộ/PII, credentials hoặc dữ liệu doanh nghiệp vào external eval service khi chưa được phép.
- LLM-as-judge có bias và variance; phải hiệu chuẩn bằng human labels, rubric rõ và kiểm tra bất đồng.
- Không tự giả định correctness chỉ từ semantic similarity, và không dùng model answer làm gold label duy nhất.
- Không thực thi tool write nguy hiểm trong eval; mock hoặc dùng sandbox với explicit approval và idempotency.

## Kỹ thuật và tài liệu tham chiếu (đọc theo nhu cầu)
- [Test matrix và test oracle](references/TEST_MATRIX.md): các tình huống mẫu, cần điều chỉnh theo nghiệp vụ có thật.
- [Tooling và recipes](references/TOOLING.md): công cụ, ví dụ triển khai, điều kiện sử dụng.
- [Nguồn chính thức](references/SOURCES.md): link chuẩn và lưu ý cách dùng từng nguồn.

Ưu tiên toolchain có sẵn trong repo. Không cài dependency mới, chỉnh migration, tạo infrastructure tốn phí hoặc gọi endpoint ngoài phạm vi nếu chưa được phép. Không sửa source production để “lách” test. Tách lỗi sản phẩm khỏi lỗi test fixture/tooling.

## Sản phẩm đầu ra
- `eval-dataset.jsonl` hoặc file dataset phù hợp chứa case_id, input, expected, tags, version.
- `eval-rubric.md`: metric definitions, grader, thresholds và handling uncertain.
- `eval-report.md`: baseline/candidate, stratified scores, traces, errors, costs, caveats và next actions.

Trong báo cáo cuối phải có: `scope`, `requirements/invariants`, `tests_added/changed`, `command`, `environment`, `expected`, `observed`, `PASS | FAIL | BLOCKED | NOT_RUN`, `evidence`, `unverified assumptions`, `risks`, `cleanup`. Không báo PASS cho test không chạy hay chỉ review tĩnh.

## Tiêu chí hoàn thành độc lập
- Dataset có provenance, case coverage và separation giữa development/holdout.
- Quality gate được xác định **trước** khi xem kết quả hoặc ghi rõ nếu exploratory.
- Có tối thiểu một kiểm chứng tool/approval/policy thực tế nếu agent được phép gọi tool.
- Báo variance, đánh giá người nếu có và chỉ đưa ra kết luận trong phạm vi dữ liệu.

## Mẫu lời gọi skill
> Hãy sử dụng `ai-agent-evaluation` để kiểm tra phạm vi [module/endpoint/flow]. Đọc yêu cầu và source hiện tại, tạo test matrix và tests phù hợp, thực thi trên [môi trường được phép] nếu khả thi, rồi trả báo cáo có bằng chứng và nêu rõ phần chưa xác minh. Không giả định các kết quả của skill nào khác.
