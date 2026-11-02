# BẢN ĐỐI SÁNH & ĐỊNH CHUẨN NĂNG LỰC HỌC THUẬT & THỰC CHIẾN CAPSTONE (SE373 & VINAI AICB COMPLIANCE MAPPING)

> **Hệ thống:** Enterprise AI Agent Platform  
> **Phiên bản:** Baseline V1.0 Greenfield Unified  
> **Cơ sở đối chuẩn:**  
> 1. Đề cương môn học **SE373 – Kỹ thuật xây dựng hệ thống Agentic AI** (Trường ĐH Công nghệ Thông tin – ĐHQG-HCM)  
> 2. Cẩm nang Lộ trình Đào tạo & Cấp phát Dự án **VinAI / VinUniversity AI Practical Competency Program (AICB)** (`MUC_LUC_LO_TRINH_CAP_DU_AN.md`, 50 bài thực chiến & đấu trường AgentArena)  
> **Mục tiêu:** Định hình ranh giới kỹ thuật, chứng minh mức độ đáp ứng 100% chuẩn đầu ra môn học, và thiết lập các kịch bản nghiệm thu thực tế (Production Benchmarks) cho Khóa luận tốt nghiệp / Đồ án Capstone.

---

## 1. Tư tưởng Cốt lõi & Khung Năng lực (Core Philosophy & Competency Framework)

Cả hai chương trình đào tạo tiên tiến (SE373 và VinAI AICB) đều thống nhất trên một nguyên lý kiến trúc nền tảng:

$$\text{Agent} = \text{Model} + \text{Harness}$$

Trong đó:
- **Model (Bộ não suy luận):** Cung cấp năng lực hiểu ngôn ngữ tự nhiên, lập luận (Reasoning), sinh văn bản có cấu trúc và đề xuất hành động (Tool Call proposals). Mô hình có tính chất bất định (probabilistic/non-deterministic), dễ bị ảo giác (hallucination) và chịu rủi ro tấn công tiêm mã (prompt injection).
- **Harness (Bộ áo giáp kiểm soát):** Là toàn bộ hạ tầng phần mềm doanh nghiệp bao quanh mô hình. Tầng Harness chịu trách nhiệm đảm bảo tính tất định (deterministic), an toàn dữ liệu đa khách hàng (RLS), điều phối trạng thái bền vững (LangGraph checkpoints), kiểm soát quyền truy cập công cụ (Single Tool Gateway), kiểm chứng trích dẫn nguồn gốc (Citation Grounding), và giám sát vận hành (Observability & Audit Logs).

**Enterprise AI Agent Platform** được thiết kế từ gốc rễ để trở thành một hệ thống **Harness Cấp Doanh nghiệp (Enterprise-Grade Harness Stack)** hoàn chỉnh, bảo vệ và vận hành bất kỳ mô hình LLM nào một cách tin cậy.

---

## 2. Ma trận Ánh xạ Chi tiết: 12 Buổi Lý thuyết & 6 Bài Lab SE373

Bảng dưới đây chứng minh sự bao phủ toàn diện 100% của hệ sinh thái tài liệu và mã nguồn dự án đối với toàn bộ chương trình SE373:

| Học phần SE373 | Khối kiến thức trọng tâm | Module / Tài liệu tương ứng trong Dự án | Mức độ đáp ứng |
|---|---|---|:---:|
| **Buổi 1: Tổng quan LLM & Agentic AI** | Triết lý `Agent = Model + Harness`; Phân biệt Chatbot vs RAG vs Workflow vs Autonomous Agent. | [SYSTEM_ARCHITECTURE.md](../02-architecture/SYSTEM_ARCHITECTURE.md)<br>[PRODUCT_REQUIREMENTS.md](./PRODUCT_REQUIREMENTS.md) | **Đạt chuẩn Enterprise** |
| **Buổi 2: Nền tảng LLM cho Agent** | Token, Context Window, Structured Output, JSON Schema, Function Calling, Schema Validation. | [SPEC-LLM-001.md](../04-module-specs/model-gateway/SPEC-LLM-001.md)<br>`contracts/jsonschema/` (44 schemas)<br>`libs/python/agent-contracts` | **Đạt chuẩn Enterprise** |
| **Buổi 3: Agent Fundamentals** | Agent Loop, ReAct (Thought-Action-Observation), Planning, Termination, Failure Recovery. | [SPEC-AGENT-001.md](../04-module-specs/agent/SPEC-AGENT-001.md)<br>[SPEC-RUNTIME-001.md](../04-module-specs/runtime/SPEC-RUNTIME-001.md) | **Đạt chuẩn Enterprise** |
| **Buổi 4: Harness Engineering** | 10 Lớp Harness: Context, Tools, Skills, Retrieval, Memory, State, Workflow, Verification, Permissions, Logging. | Phân rã độc lập qua 13 Module Specs và 13 ADRs kiến trúc. | **Đạt chuẩn Enterprise** |
| **Buổi 5: Tool Use & Skills Engineering** | Tool Schemas, 12-Step ToolDispatcher, Retry/Timeout, Idempotency, Chuẩn đóng gói `SKILL.md`. | [SPEC-TOOL-001.md](../04-module-specs/tool/SPEC-TOOL-001.md)<br>[SPEC-SKILL-001.md](../04-module-specs/skill/SPEC-SKILL-001.md)<br>`examples/skills/weekly-report/` | **Đạt chuẩn Enterprise** |
| **Buổi 6: Enterprise RAG** | Ingestion, Chunking, Embedding, Vector Store, Hybrid Search, Citation Verifier, Chống Hallucination. | [SPEC-KNOWLEDGE-001.md](../04-module-specs/knowledge/SPEC-KNOWLEDGE-001.md)<br>`apps/agent-runtime/src/agent_runtime/knowledge/` | **Đạt chuẩn Enterprise** |
| **Buổi 7: Memory & State** | Short-term (Checkpoints), Long-term Memory (Episodic/Semantic), Consent, TTL, Context Compaction. | [SPEC-MEMORY-001.md](../04-module-specs/memory/SPEC-MEMORY-001.md)<br>[SPEC-RUNTIME-001.md](../04-module-specs/runtime/SPEC-RUNTIME-001.md) | **Đạt chuẩn Enterprise** |
| **Buổi 8: Workflow Orchestration** | LangGraph `StateGraph`, Node Routing, PostgreSQL Checkpointing, Human-in-the-loop (HITL) Approvals. | [SPEC-WORKFLOW-001.md](../04-module-specs/workflow/SPEC-WORKFLOW-001.md)<br>`apps/agent-runtime/src/agent_runtime/application/` | **Đạt chuẩn Enterprise** |
| **Buổi 9: Verification & Evaluation** | Golden Dataset, Graders, LLM-as-a-Judge, Trajectory Evaluation, Release Gates. | [SPEC-EVAL-001.md](../04-module-specs/evaluation/SPEC-EVAL-001.md)<br>`evals/datasets/golden_tasks.v1.jsonl`<br>`evals/rubrics/tool_use.md` | **Đạt chuẩn Enterprise** |
| **Buổi 10: Multi-Agent & MCP** | Supervisor-Worker, Model Context Protocol (MCP Gateway, FastMCP), SSRF Defense, OAuth Audience. | [SPEC-MCP-001.md](../04-module-specs/mcp/SPEC-MCP-001.md)<br>`apps/agent-runtime/src/agent_runtime/mcp/client.py` | **Đạt chuẩn Enterprise** |
| **Buổi 11: Security & Governance** | RLS trên 55 bảng, Zero-Width Steganography Shield, Vietnamese PII Scrubber, OpenTelemetry WORM Audit. | [SPEC-SECURITY-001.md](../04-module-specs/security/SPEC-SECURITY-001.md)<br>[OBSERVABILITY_GUIDE.md](../08-operations/OBSERVABILITY_GUIDE.md) | **Đạt chuẩn Enterprise** |
| **Buổi 12: Đồ án & Triển khai** | Monorepo CI/CD, Containerization, Database Migration, Runbook kiểm soát Gates G0 - G12. | [SPEC-DEPLOY-001.md](../04-module-specs/deploy/SPEC-DEPLOY-001.md)<br>[IMPLEMENTATION_RUNBOOK.md](../09-implementation/IMPLEMENTATION_RUNBOOK.md) | **Đạt chuẩn Enterprise** |

---

## 3. Tích hợp Các Kịch bản Nghiệp vụ Thực chiến từ VinAI AICB (50 Ngày & Đấu trường)

Để biến các bài toán thực hành từ `MUC_LUC_LO_TRINH_CAP_DU_AN.md` thành các kịch bản kiểm thử mẫu (Test Scenarios & Golden Benchmarks) trên nền tảng, hệ thống tích hợp 6 kịch bản tiêu biểu:

### Kịch bản 1: Hội đồng Đa Tác tử Phân xử Khiếu nại TMĐT Olist (`VIN-P1-D09B`)
- **Bối cảnh:** Sàn thương mại điện tử tiếp nhận 50 ca khiếu nại bồi thường phức tạp đòi hỏi đối soát chéo 9 bảng dữ liệu quan hệ (đơn hàng, thanh toán, vận chuyển, đánh giá, người bán).
- **Hiện thực hóa trên Nền tảng:**
  - Điều phối qua `SPEC-WORKFLOW-001` và LangGraph `StateGraph`: `DisputeCoordinator` phân công song song cho `DeliverySpecialist`, `PaymentSpecialist` và `CustomerSpecialist`.
  - Toàn bộ kết quả truy vấn dữ liệu được thực thi thông qua Tool Gateway (`SPEC-TOOL-001`) đọc CSDL Olist dạng chỉ đọc (Read-only SQL).
  - Tác tử `RulingAgent` tổng hợp và đưa ra quyết định bồi thường theo đúng schema `DisputeCaseOutput`. Nếu số tiền bồi hoàn vượt ngưỡng quy định, kích hoạt quy trình HITL (`approval_requests`) chờ nhân sự quản lý phê duyệt.

### Kịch bản 2: Đường ống Tri thức Tự Phục hồi & Tiêm lỗi Dữ liệu (`VIN-P1-D10`)
- **Bối cảnh:** Dữ liệu học thuật thô từ Crossref API bị lỗi (mất abstract, trùng DOI, nhiễu văn bản) làm suy giảm độ chính xác của RAG.
- **Hiện thực hóa trên Nền tảng:**
  - Hệ thống Ingestion của `SPEC-KNOWLEDGE-001` lưu vết raw artifacts trong Object Storage trước khi chuyển hóa sang `knowledge.chunks` và `knowledge.vector_refs`.
  - Bộ kiểm định chất lượng dữ liệu phát hiện Schema Drift và kích hoạt `repair_pipeline` tái nạp và sửa lỗi chunking tự động mà không làm gián đoạn việc phục vụ của Agent.

### Kịch bản 3: Pháo đài An ninh Đa tầng & Bộ lọc PII Tiếng Việt (`VIN-P1-D11` & `VIN-T2-D24`)
- **Bối cảnh:** Ngăn chặn tấn công Indirect Prompt Injection tàng hình qua ký tự vô hình (Zero-Width Unicode) và rò rỉ dữ liệu định danh khách hàng.
- **Hiện thực hóa trên Nền tảng:**
  - Middleware An ninh của `SPEC-SECURITY-001` chặn đứng ký tự ẩn qua `ZeroWidthShield` trước khi đưa prompt vào Model Gateway.
  - Module `PiiScrubber` sử dụng mẫu biểu thức regex và mô hình NER tiếng Việt nhận diện và che giấu tự động số CCCD (12 chữ số), số điện thoại di động (10 chữ số) và mã số thẻ thành `[REDACTED_*]` trước khi ghi vào log hoặc gửi tới nhà cung cấp LLM bên thứ ba.
  - Phân tích cú pháp trừu tượng `ast` chặn đứng mọi nỗ lực thực thi mã lệnh shell nguy hiểm (`eval`, `exec`, `os.system`).

### Kịch bản 4: Hệ sinh thái FastMCP & Khám phá Công cụ Chuẩn hóa (`VIN-T3-D26A`)
- **Bối cảnh:** Doanh nghiệp chuyển dịch từ các hàm API rời rạc sang giao thức mở Model Context Protocol (Anthropic MCP Standard).
- **Hiện thực hóa trên Nền tảng:**
  - `SPEC-MCP-001` triển khai `McpClientAdapter` kết nối với các MCP Servers qua giao thức Streamable HTTP / JSON-RPC 2.0.
  - Cổng an ninh MCP tích hợp bộ lọc chống tấn công giả mạo yêu cầu từ phía máy chủ (SSRF Defense), chặn hoàn toàn các dải IP nội bộ (`127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.169.254`).
  - Mọi công cụ do MCP Server cung cấp được ánh xạ và phê duyệt qua bảng `integration.mcp_tool_mappings` trước khi cấp phép cho Agent sử dụng.

### Kịch bản 5: Bộ Đánh giá Đa Phân phối 50 Câu & Tầng Guardrail Kép (`VIN-T3-D24`)
- **Bối cảnh:** Kiểm định hệ sinh thái RAG trước khi phát hành với 50 câu hỏi trải dài trên 3 phân phối: 20 câu In-Domain, 20 câu Edge-Cases và 10 câu Adversarial/Bẫy.
- **Hiện thực hóa trên Nền tảng:**
  - Module `SPEC-EVAL-001` tích hợp bộ chấm điểm tự động phân loại lỗi theo ma trận 4x3 (Lỗi tầng Retrieval vs Lỗi tầng Generation).
  - Tự động trích xuất danh sách 10 câu có điểm số thấp nhất (Bottom-10 Diagnosis) để phục vụ việc cải thiện chất lượng chunking và tinh chỉnh prompt.
  - Thiết lập Guardrail hai đầu: Chặn đứng câu hỏi độc hại tại đầu vào (Input Guardrail) và từ chối câu trả lời có độ trung thực (Faithfulness) dưới 0.70 tại đầu ra (Output Guardrail).

### Kịch bản 6: Đấu trường AgentArena — Bộ giáp Middleware 5 Tầng (`VIN-COMP-ARENA`)
- **Bối cảnh:** "Thuần hóa" một tác tử AI yếu kém, nhiều lỗi bằng lớp Middleware bọc bên ngoài mà không sửa đổi prompt hay trọng số mô hình.
- **Hiện thực hóa trên Nền tảng:**
  - Khung kiến trúc Harnessing Stack của nền tảng hỗ trợ 6 điểm móc vòng đời thực thi: `pre_run`, `post_run`, `pre_tool_call`, `post_tool_call`, `on_tool_error`, `on_agent_stall`.
  - Áp dụng trọn vẹn 5 tầng phòng thủ:
    1. *Grounding Shield:* Đối chiếu xác minh trích dẫn trong `SPEC-KNOWLEDGE-001`.
    2. *Security Guard:* Chặn prompt injection và rà quét tài liệu nhiễm độc trong `SPEC-SECURITY-001`.
    3. *Budget Controller:* Cưỡng chế trần số bước `max_steps` và quota token trong `SPEC-LLM-001`.
    4. *Tool Corruption Sanitizer:* Bắt và làm sạch payload rác từ công cụ trong `SPEC-TOOL-001`.
    5. *Lifecycle Coordinator:* Quản lý trạng thái và phục hồi sự cố trong `SPEC-RUNTIME-001`.
  - Mục tiêu nghiệm thu đạt chuẩn Tam giác Vàng: **Grounding $\ge 90\%$**, **Safety $= 100\%$**, **Efficiency $\ge 80\%$**.

---

## 4. Checklist Thẩm định Đồ án Cuối kỳ & Khóa luận Tốt nghiệp (Rubric 9–10 Điểm)

Bảng checklist dưới đây là căn cứ chính thức để hội đồng chấm điểm đánh giá đồ án:

| Tiêu chuẩn Đánh giá | Yêu cầu Tối thiểu | Hiện thực hóa trong Dự án | Bằng chứng Kiểm thử (Quality Gate) |
|---|---|---|---|
| **1. Model Integration** | Đa nhà cung cấp, Structured Output, Function Calling, Schema Validation. | Model Gateway Decoupled, 44 JSON Schemas, Pydantic Contract Models. | `pytest tests/contract/` **PASS (7/7)** |
| **2. Agentic Workflow** | $\ge 3$ nodes, có Goal, Termination Condition rõ ràng, phục hồi lỗi. | LangGraph StateGraph, giới hạn `limits.max_steps`, ngắt mạch tự động. | `apps/agent-runtime/src/agent_runtime/application/state_graph.py` |
| **3. Harness Architecture** | Thể hiện rõ `Agent = Model + Harness`, $\ge 4$ lớp harness bảo vệ. | 10 Lớp Harness độc lập (Tools, Skills, MCP, RAG, Memory, Security...). | Hệ thống 13 Module Specs & Kiến trúc Dual-Plane |
| **4. Tool Use & Skills** | $\ge 1$ external tool, $\ge 2$ reusable skills, có input/output contract. | 12-Step ToolDispatcher, Chuẩn đóng gói `SKILL.md` (Weekly Report Skill). | `examples/skills/weekly-report/SKILL.md` |
| **5. Enterprise RAG** | Ingestion, chunking, pgvector HNSW, Hybrid Search, Citation Verifier. | Module RAG với PostgreSQL `pgvector`, BM25, Citation Overlap Verifier. | `SPEC-KNOWLEDGE-001.md` |
| **6. Memory & State** | Session Checkpoints, Long-term Memory, Context Compaction, Consent. | LangGraph checkpointer vào DB-56 (`runtime.checkpoints`), PII masking. | `SPEC-MEMORY-001.md` |
| **7. Workflow Orchestration** | LangGraph StateGraph, Checkpointing, Recovery, Human Approval (HITL). | Cổng duyệt người dùng có xác thực HMAC Action Hash, Worker tự phục hồi. | `tests/recovery/test_resume_after_restart.py` |
| **8. Multi-Agent & MCP** | Hỗ trợ tương tác đa tác tử hoặc chuẩn mở Model Context Protocol (MCP). | MCP Gateway (FastMCP / JSON-RPC 2.0), Chống SSRF mạng nội bộ. | `SPEC-MCP-001.md` & `tests/contract/test_skill_and_mcp_contracts.py` |
| **9. Verification & Evaluation** | Golden Dataset, Definition of Done, Graders, Trajectory Eval, Regression Gate. | Bộ dataset chuẩn vàng `golden_tasks.v1.jsonl`, rubric `tool_use.md`. | `evals/datasets/` & `SPEC-EVAL-001.md` |
| **10. Security & Governance** | RLS trên 55 bảng, Zero-Width Shield, PII Scrubber, WORM Audit Logs. | PostgreSQL Force RLS, AST Sandbox, Log phân tán OpenTelemetry. | `docs/03-database/database/validate_db_static.py` **PASS** |

---

## 5. Kết luận & Khuyến nghị Lộ trình Báo cáo

Toàn bộ hệ thống kỹ thuật của **Enterprise AI Agent Platform** đã được xây dựng theo các tiêu chuẩn khắt khe nhất của cả môi trường đại học chính quy (SE373) và chương trình thực chiến doanh nghiệp (VinAI AICB).

Khi trình bày báo cáo Khóa luận hoặc Đồ án môn học, nhóm thực hiện nên nhấn mạnh 3 điểm tạo nên sự khác biệt vượt trội:
1. **Kiến trúc Harnessing Cấp Doanh nghiệp:** Không đơn thuần viết prompt và gọi API rời rạc, mà xây dựng một nền tảng quản trị vòng đời tác tử bền vững (`Agent = Model + Harness`).
2. **Cơ chế Gọi Công cụ Đơn nhất & Tính Bất biến Tuyệt đối:** Giải quyết bài toán bảo vệ hệ thống ngoài bằng quy trình 12 bước `ToolDispatcher` với khóa Idempotency duy nhất `(tenant_id, workspace_id, operation_key)`.
3. **An ninh Dữ liệu Đa Khách hàng Cưỡng chế:** Đạt cấp độ bảo mật cao nhất bằng PostgreSQL Row-Level Security trên 55 bảng CSDL và cơ chế bảo vệ kép chống tấn công tiêm mã ẩn (Zero-Width Steganography Shield).

