# DOCUMENT INDEX — MASTER SYSTEM DOCUMENTATION V1.0

> **Hệ thống:** Enterprise AI Agent Platform  
> **Cập nhật:** 2026-10-30  
> **Kiến trúc:** Baseline V1.0 Greenfield Unified (56 bảng / 7 schemas / 55 RLS / 10 immutability triggers)
> **Nguyên tắc:** Schema-First, Zero Plaintext Secrets, Strict Trust Boundaries, 12-Step ToolDispatcher.

---

## 1. Cấu trúc Cây Thư mục Tài liệu (Documentation Structure)

Bộ tài liệu được phân thành 10 thư mục chuẩn hóa theo mô hình kiến trúc doanh nghiệp:

```text
docs/
├── 00-governance/           # Quản trị tài liệu, nguồn chân lý & sổ đăng ký quyết định kiến trúc
├── 01-product/              # Yêu cầu sản phẩm (PRD), tầm nhìn & lộ trình phát triển (Roadmap)
├── 02-architecture/         # Kiến trúc hệ thống tổng thể, cấu trúc repository & các ADRs
├── 03-database/             # Thiết kế CSDL 56 bảng, từ điển dữ liệu & chiến lược chỉ mục
├── 04-module-specs/         # Đặc tả kỹ thuật chi tiết 13 phân hệ (Agent, Runtime, Tool, Skill, MCP...)
├── 05-change-requests/      # Hồ sơ Change Request & đặc tả nâng cấp nền tảng (Skill, MCP)
├── 06-api-contracts/        # Chuẩn thiết kế API, mã lỗi RFC 9457 & thông số sự kiện SSE
├── 07-testing/              # Chiến lược kiểm thử, ma trận bảo mật & kiểm thử phục hồi
├── 08-operations/           # Vận hành, giám sát phân tán (Observability) & ứng phó sự cố (DR)
└── 09-implementation/       # Runbook thực thi từng bước có kiểm chứng (Gates G0 - G12)
```

---

## 2. Danh mục Tài liệu Chi tiết

| Thư mục | Tên tài liệu | Nội dung chính |
|---|---|---|
| **00-governance** | `DOCUMENT_INDEX.md` | Bản mục lục tổng thể hệ thống tài liệu. |
| | `SOURCE_OF_TRUTH.md` | Định nghĩa các nguồn chân lý cho schema, code, DB và tài liệu. |
| | `DECISION_REGISTER.md` | Sổ đăng ký 13 quyết định kiến trúc cốt lõi (ADR-001 -> ADR-013). |
| | `CANONICAL_DATABASE_56.md` | Danh mục 56 bảng CSDL chuẩn hóa phân theo 7 schemas. |
| | `MODULE_SPEC_INDEX.md` | Chỉ mục và ma trận phụ thuộc của 13 bản đặc tả phân hệ. |
| | `IMPLEMENTATION_ORDER_FULL_56.md` | Trình tự thực thi và triển khai các gói theo chuẩn 56 bảng. |
| | [`../../AGENT_CODING_RULES.md`](../../AGENT_CODING_RULES.md) | Hướng dẫn nhanh cho AI Coding Agents bắt đầu công việc. |
| | `AGENT_COLLABORATION_AND_BRANCHING.md` | Quy ước nhánh, phạm vi sở hữu, PR và bằng chứng dành cho agent/engineer. |
| | VINAI_CAPSTONE_CLUSTER_INDEX.json | Bản đồ chỉ mục máy đọc: Gom cụm 50 bài học VinAI Capstone vào 8 cụm chuyên môn. |
| | VINAI_CAPSTONE_AGENT_EXECUTION_GUIDE.md | Hướng dẫn chuẩn hóa dành cho AI Coding Agent đọc và áp dụng 50 bài học. |
| **01-product** | `PRODUCT_REQUIREMENTS.md` | Đặc tả yêu cầu sản phẩm (PRD): Persona, Skills, Tools, HITL, Workflows. |
| | `ROADMAP.md` | Lộ trình triển khai qua 4 mốc (Milestone A -> D). |
| | `SE373_COURSE_CONTEXT.md` | Bối cảnh học phần SE373 và roadmap học tập/thực hành liên quan đến đồ án. |
| | ACADEMIC_AND_CAPSTONE_COMPLIANCE_MAPPING.md | Bản đối sánh & định chuẩn năng lực SE373 và VinAI Capstone (50 bài học & AgentArena). |
| **02-architecture** | `SYSTEM_ARCHITECTURE.md` | Kiến trúc 6 Trụ cột Nền tảng, Dual-Plane Monolith, Sequence flows. |
| | `REPOSITORY_STRUCTURE.md` | Catalog chi tiết mã nguồn monorepo (`apps/`, `contracts/`, `libs/`). |
| | `ENTERPRISE_AGENT_PLATFORM_REPOSITORY_IMPLEMENTATION_V1.md` | Bản đặc tả kiến trúc gốc toàn diện 26 chương (1.056 dòng). |
| | dr/ | Thư mục chứa 14 tệp ADR độc lập từ ADR-001 đến ADR-014 (bao gồm ADR-014 Kafka Integration). |
| **03-database** | `DATABASE_DESIGN.md` | Thiết kế CSDL 56 bảng, ERD, Transaction boundaries, RLS model. |
| | `DATA_DICTIONARY.md` | Từ điển dữ liệu chi tiết toàn bộ 56 bảng thuộc 7 schemas. |
| | `INDEX_STRATEGY.md` | Chiến lược đánh chỉ mục B-tree/BRIN, composite tenant indexes. |
| **04-module-specs** | `agent/SPEC-AGENT-001.md` | Đặc tả Agent lifecycle, Spec versioning, immutable publishing. |
| | `runtime/SPEC-RUNTIME-001.md`| Đặc tả Execution Engine, LangGraph persistence, state machine. |
| | `workflow/SPEC-WORKFLOW-001.md` | Đặc tả đồ thị công việc, Digital Workers, node routing. |
| | `tool/SPEC-TOOL-001.md` | Đặc tả Tool Gateway, 12-Step ToolDispatcher, Idempotency store. |
| | `security/SPEC-SECURITY-001.md` | Đặc tả bảo mật đa tenant, Force RLS, Zero-width shield, PII filter. |
| | `skill/SPEC-SKILL-001.md` | Đặc tả Skill Engine, on-demand loading, token containment, sandbox. |
| | `mcp/SPEC-MCP-001.md` | Đặc tả Model Context Protocol, Streamable HTTP/stdio, SSRF filter. |
| | `knowledge/SPEC-KNOWLEDGE-001.md` | Đặc tả kho tri thức RAG, chunking, vector embedding & ACL. |
| | `memory/SPEC-MEMORY-001.md` | Đặc tả hội thoại ngắn hạn/dài hạn, sự đồng thuận & TTL. |
| | `model-gateway/SPEC-LLM-001.md` | Đặc tả Gateway LLM, rate limits, quota, token meter. |
| | `evaluation/SPEC-EVAL-001.md` | Đặc tả bộ đánh giá Golden Dataset, Eval rubrics, LLM-as-judge. |
| | `studio/SPEC-STUDIO-001.md` | Đặc tả Next.js Studio Console, feature modules, form adapters. |
| | `deploy/SPEC-DEPLOY-001.md` | Đặc tả CI/CD pipelines, Docker, migration rollout, recovery. |
| **05-change-requests** | `CR-SKILL-001.md` | Đặc tả nền tảng: Agent Skill Management (On-demand loading). |
| | `CR-MCP-001.md` | Đặc tả nền tảng: Model Context Protocol (MCP Gateway & SSRF shield). |
| **06-api-contracts** | `API_DESIGN_GUIDELINES.md` | Tiêu chuẩn REST API, Error envelope, Idempotency-Key. |
| | `EVENT_SPECIFICATIONS.md` | Đặc tả luồng Server-Sent Events (SSE) theo dõi run timeline. |
| **07-testing** | `TEST_STRATEGY.md` | Kim tự tháp kiểm thử, Contract testing, RLS negative testing. |
| | CI_CD_CT_PIPELINE.md | Đặc tả quy trình tự động hóa CI/CD/CT 4 cổng kiểm soát (Gate 1 -> Gate 4, RAGAS, AgentArena, Champion registry). |
| | `validation-scripts/` | Bộ công cụ kiểm thử tĩnh tính toàn vẹn 13 SPECs & 56 bảng. |
| **08-operations** | `OBSERVABILITY_GUIDE.md` | OpenTelemetry distributed tracing, WORM audit logging. |
| | DISASTER_RECOVERY.md | Runbook DR SRE (RTO < 30s, RPO < 60s, Multi-Region, SIGSTOP/SIGKILL, Google SRE postmortem template). |
| | FINOPS_GPU_CALCULATOR.md | Hướng dẫn tính toán FinOps & Serving (Roofline model, MFU, MBU, PagedAttention, Unit economics $/1M tokens). |
| **09-implementation** | `IMPLEMENTATION_RUNBOOK.md`| Runbook thực thi 56 bảng, kiểm soát chất lượng qua Gates G0 - G12. |
