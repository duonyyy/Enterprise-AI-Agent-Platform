# Nguồn chính thức — ai-agent-evaluation

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## OpenAI Agent Evals
- URL: https://developers.openai.com/api/docs/guides/agent-evals
- Dùng để: Trace grading/dataset để kiểm tra agent workflow.

## OpenAI Trace Grading
- URL: https://developers.openai.com/api/docs/guides/trace-grading
- Dùng để: Gắn label/score theo tool call và execution trace.

## LangSmith Evaluation Types
- URL: https://docs.langchain.com/langsmith/evaluation-types
- Dùng để: Offline/online eval, code graders, LLM-as-judge và summary metric.

## Ragas Available Metrics
- URL: https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/
- Dùng để: RAG faithfulness/context precision và agent tool evaluation.

## OpenAI Evals transition notice
- URL: https://developers.openai.com/api/docs/guides/evals
- Dùng để: Nền tảng Evals cũ có lộ trình ngừng hoạt động; không phụ thuộc API legacy.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
