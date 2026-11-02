# Coding Standards & Architecture Rules — Production v1.0
## (Unified Enterprise Specification)

> **Dự án / Project:** Enterprise AI Agent Platform (độc lập; không phải RetailMind)  
> **Đối tượng / Audience:** Coding Agents, Backend/Frontend Engineers, Reviewers, DevSecOps  
> **Trạng thái / Status:** APPROVED POLICY (v1.0 Baseline)  
> **Cập nhật / Last updated:** 2026-11-01  
> **Baseline CSDL:** Target **56 domain tables / 7 PostgreSQL schemas** (`iam`, `catalog`, `knowledge`, `runtime`, `integration`, `governance`, `ops`)  
> **Tài liệu tham chiếu:** `AGENT_CODING_RULES.md`; 13 `SPEC-*.md`; `CR-SKILL-001`; `CR-MCP-001`.  
> **Định vị tài liệu:** Bản quy chuẩn kỹ thuật toàn diện thống nhất (**Unified Master Standard**) tích hợp đầy đủ cả **Tầng Kiến trúc Vĩ mô (Macro Architecture Invariants & Governance)** lẫn **Tầng Kỹ thuật Lập trình Vi mô (Micro Coding Techniques & Code Style)**.

---

## 📌 Mục lục (Table of Contents)

### PHẦN I: NGUYÊN TẮC CỐT LÕI & KIẾN TRÚC HỆ THỐNG (MACRO ARCHITECTURE)
- [0. Mục tiêu, Phạm vi & Ngôn ngữ Quy chuẩn](#0-mục-tiêu-phạm-vi--ngôn-ngữ-quy-chuẩn)
- [1. Bất biến Kiến trúc — AR-001 đến AR-014](#1-bất-biến-kiến-trúc--ar-001-đến-ar-014)
- [2. Hợp đồng Dữ liệu, API & Sự kiện — CTR-001 đến CTR-012](#2-hợp-đồng-dữ-liệu-api--sự-kiện--ctr-001-đến-ctr-012)
- [3. Tiêu chuẩn Cơ sở Dữ liệu & Persistence — DB-001 đến DB-013](#3-tiêu-chuẩn-cơ-sở-dữ-liệu--persistence--db-001-đến-db-013)
- [4. AI Runtime, Skill Engine, MCP & Tools — AI-001 đến AI-012](#4-ai-runtime-skill-engine-mcp--tools--ai-001-đến-ai-012)
- [5. An toàn Bảo mật & Chuỗi Cung ứng — SEC-001 đến SEC-010](#5-an-toàn-bảo-mật--chuỗi-cung-ứng--sec-001-đến-sec-010)

### PHẦN II: KỸ THUẬT LẬP TRÌNH & PHONG CÁCH MÃ NGUỒN (MICRO CODING TECHNIQUES)
- [6. Quy ước Đặt tên (Naming Conventions) — CT-001 đến CT-010](#6-quy-ước-đặt-tên-naming-conventions--ct-001-đến-ct-010)
- [7. Thiết kế Hàm, Method & Kiểm soát Luồng (Control Flow) — CT-011 đến CT-020](#7-thiết-kế-hàm-method--kiểm-soát-luồng-control-flow--ct-011-đến-ct-020)
- [8. Chuẩn Python & Kỹ thuật Lập trình — PY-001..PY-016 & CT-021..CT-045](#8-chuẩn-python--kỹ-thuật-lập-trình--py-001py-016--ct-021ct-045)
  - [8.1. Nguyên tắc cốt lõi: PY-001 đến PY-016](#81-nguyên-tắc-cốt-lõi-py-001-đến-py-016)
  - [8.2. Typing, Dataclasses & Models: CT-021 đến CT-029](#82-typing-dataclasses--models-ct-021-đến-ct-029)
  - [8.3. Xử lý Lỗi & Logging Có Cấu trúc: CT-030 đến CT-038](#83-xử-lý-lỗi--logging-có-cấu-trúc-ct-030-đến-ct-038)
  - [8.4. Lập trình Bất đồng bộ & Concurrency: CT-039 đến CT-045](#84-lập-trình-bất-đồng-bộ--concurrency-ct-039-đến-ct-045)
- [9. Chuẩn TypeScript, React & Next.js — TS-001..TS-016 & CT-061..CT-078](#9-chuẩn-typescript-react--nextjs--ts-001ts-016--ct-061ct-078)
  - [9.1. Nguyên tắc cốt lõi: TS-001 đến TS-016](#91-nguyên-tắc-cốt-lõi-ts-001-đến-ts-016)
  - [9.2. TypeScript Types, Strictness & Error Handling: CT-061 đến CT-070](#92-typescript-types-strictness--error-handling-ct-061-đến-ct-070)
  - [9.3. Kiến trúc Next.js App Router & React Components: CT-071 đến CT-078](#93-kiến-trúc-nextjs-app-router--react-components-ct-071-đến-ct-078)
- [10. Kỹ thuật Lập trình Persistence & SQL — CT-046 đến CT-053](#10-kỹ-thuật-lập-trình-persistence--sql--ct-046-đến-ct-053)
- [11. Kỹ thuật Lập trình FastAPI, Validation & HTTP — CT-054 đến CT-060](#11-kỹ-thuật-lập-trình-fastapi-validation--http--ct-054-đến-ct-060)
- [12. Pattern Lập trình Agent Runtime, Skills & MCP — CT-079 đến CT-086](#12-pattern-lập-trình-agent-runtime-skills--mcp--ct-079-đến-ct-086)

### PHẦN III: ĐẢM BẢO CHẤT LƯỢNG, CÔNG CỤ & QUY TRÌNH PULL REQUEST
- [13. Chiến lược & Kỹ thuật Kiểm thử — QA-001..QA-014 & CT-087..CT-096](#13-chiến-lược--kỹ-thuật-kiểm-thử--qa-001qa-014--ct-087ct-096)
  - [13.1. Merge Gates & Kim tự tháp Kiểm thử: QA-001 đến QA-014](#131-merge-gates--kim-tự-tháp-kiểm-thử-qa-001-đến-qa-014)
  - [13.2. Kỹ thuật Viết Test Thực chiến: CT-087 đến CT-096](#132-kỹ-thuật-viết-test-thực-chiến-ct-087-đến-ct-096)
- [14. Hiệu năng, An toàn & Bảng Antipatterns Chặn Merge — CT-097..CT-104](#14-hiệu-năng-an-toàn--bảng-antipatterns-chặn-merge--ct-097ct-104)
  - [14.1. Performance & Safety: CT-097 đến CT-104](#141-performance--safety-ct-097-đến-ct-104)
  - [14.2. Bảng 12 Antipatterns Chặn Merge](#142-bảng-12-antipatterns-chặn-merge)
- [15. Vận hành, Giám sát & Khả năng Quan sát — OPS-001 đến OPS-008](#15-vận-hành-giám-sát--khả-năng-quan-sát--ops-001-đến-ops-008)
- [16. Cấu trúc Repository, Quyền Sở hữu & Quy ước File](#16-cấu-trúc-repository-quyền-sở-hữu--quy-ước-file)
- [17. Cấu hình Công cụ Chuẩn (Tooling Reference)](#17-cấu-hình-công-cụ-chuẩn-tooling-reference)
- [18. Quy trình PR, Checklist Đánh giá Mã Nguồn & Prompt Giao việc](#18-quy-trình-pr-checklist-đánh-giá-mã-nguồn--prompt-giao-việc)
  - [18.1. PR Template & Bảng Bằng chứng Evidence](#181-pr-template--bảng-bằng-chứng-evidence)
  - [18.2. Code Review Checklist (12 Tiêu chí)](#182-code-review-checklist-12-tiêu-chí)
  - [18.3. Prompt Mẫu Giao việc Cho Coding Agent](#183-prompt-mẫu-giao-việc-cho-coding-agent)
- [19. Lộ trình Áp dụng (Adoption Roadmap) & Definition of Done](#19-lộ-trình-áp-dụng-adoption-roadmap--definition-of-done)
- [20. Nguồn Tham khảo Kỹ thuật Chính thức](#20-nguồn-tham-khảo-kỹ-thuật-chính-thức)

---

# PHẦN I: NGUYÊN TẮC CỐT LÕI & KIẾN TRÚC HỆ THỐNG (MACRO ARCHITECTURE)

## 0. Mục tiêu, Phạm vi & Ngôn ngữ Quy chuẩn

Tài liệu này thiết lập **bộ quy chuẩn kỹ thuật toàn diện thống nhất (Unified Engineering Standard v1.0)** cho hệ thống Enterprise AI Agent Platform. Văn kiện hợp nhất toàn bộ các quy tắc kiến trúc sản xuất (Macro Architecture Rules) và kỹ thuật lập trình vi mô (Micro Coding Techniques) để định hướng cho cả kỹ sư phần mềm lẫn AI Coding Agents.

### 0.1. Ngôn ngữ Quy chuẩn (Normative Interpretation)
- **MUST / MUST NOT**: Ràng buộc tuyệt đối; vi phạm sẽ **chặn merge (merge blocker)**.
- **SHOULD / SHOULD NOT**: Yêu cầu mặc định; mọi ngoại lệ phải có Architecture Decision Record (ADR) được phê duyệt hoặc văn bản giải trình rủi ro trong PR.
- **MAY**: Tùy chọn, áp dụng khi phù hợp với acceptance criteria đã ghi nhận.
- **HEURISTIC**: Tín hiệu cảnh báo cần xem xét khi code review; không phải lint rule cứng.
- **BLOCKED**: Trạng thái một tác vụ không thể tiếp tục an toàn do thiếu hợp đồng, bằng chứng hoặc môi trường kiểm thử.

> **Quy tắc Bằng chứng Thực tế:** Tuyệt đối không giả mạo trạng thái `PASS` khi công cụ, dịch vụ, cơ sở dữ liệu hay môi trường chưa sẵn sàng. Bắt buộc phải báo cáo trung thực là `NOT_RUN` hoặc `BLOCKED` kèm bằng chứng lệnh thực thi.

### 0.2. Thứ tự Ưu tiên khi Tài liệu có Mâu thuẫn (Precedence)
1. Hiện trạng triển khai thực tế đã kiểm chứng và các migrations đã được áp dụng.
2. Các quyết định kiến trúc đã phê duyệt ([ADR-001 đến ADR-013](../02-architecture/adr/)), hợp đồng dữ liệu versioned (`contracts/jsonschema/`, `contracts/openapi/`) và kế hoạch CSDL chuẩn hóa.
3. Tài liệu quản trị [`SOURCE_OF_TRUTH.md`](./SOURCE_OF_TRUTH.md), kế hoạch di trú **DB-56** và các bản đặc tả module [`SPEC-*.md`](../04-module-specs/) / Change Requests ([`CR-SKILL-001`](../05-change-requests/CR-SKILL-001.md), [`CR-MCP-001`](../05-change-requests/CR-MCP-001.md)).
4. [`AGENT_CODING_RULES.md`](../../AGENT_CODING_RULES.md) về an toàn, ranh giới tin cậy; và **tài liệu này** về chất lượng code, kiến trúc và phong cách lập trình.
5. Các ví dụ minh họa và runbook hướng dẫn.

### 0.3. Baseline Kiến trúc Nền tảng
- **Control Plane:** `apps/control-api` — Kiến trúc Modular Monolith xây dựng trên Python + FastAPI.
- **Execution Plane:** `apps/agent-runtime` — Python + LangGraph được trừu tượng hóa qua ranh giới adapter/engine.
- **Presentation Plane:** `apps/studio-web` — Next.js 15 App Router + TypeScript; thẩm quyền nghiệp vụ luôn nằm ở phía máy chủ.
- **Hợp đồng Dùng chung:** `contracts/` — JSON Schema có phiên bản, OpenAPI và các sự kiện SSE.
- **Công cụ Quản lý Gói Python:** `uv` workspace với tệp khóa duy nhất `uv.lock`.
- **Công cụ Quản lý Gói JS/TS:** `pnpm` workspace với tệp khóa duy nhất `pnpm-lock.yaml`.
- **Cơ sở Dữ liệu:** PostgreSQL là nguồn chân lý duy nhất (Greenfield Unified Baseline V1.0 gồm 56 bảng / 7 schemas). Qdrant chỉ đóng vai trò chỉ mục tìm kiếm vector phái sinh, không có thẩm quyền cấp phép.

### 0.4. Kết quả Mong muốn từ Mỗi Pull Request (PR)
1. Diff nhỏ gọn, code dễ đọc; quy ước đặt tên thống nhất; không sao chép logic nghiệp vụ.
2. Mọi input/output đều có types rõ ràng; đầu vào bên ngoài được xác thực ở ranh giới; lỗi có cấu trúc; tác động phụ (side effects) được kiểm soát.
3. Tests chứng minh *hành vi* nghiệp vụ và các trường hợp biên (edge cases), không chạy theo tỉ lệ % coverage ảo.
4. Formatting, lint, typecheck và targeted tests đều chạy thực tế; bằng chứng lệnh thật được đính kèm trong PR.

---

## 1. Bất biến Kiến trúc — AR-001 đến AR-014

**AR-001 — Quyền sở hữu nghiệp vụ (Business Ownership):** Mỗi quy tắc nghiệp vụ có **duy nhất một module sở hữu**. Các module khác giao tiếp qua use cases được export, typed ports, hoặc hợp đồng dịch vụ/sự kiện có phiên bản; tuyệt đối không chọc thẳng vào ORM model hay repository nội bộ của nhau. Nghiêm cấm tạo thư mục "bãi rác" `utils/` hoặc các god-services chứa mọi thứ.

**AR-002 — Chiều phụ thuộc (Dependency Direction):** Tầng chính sách cốt lõi và domain types **không được phụ thuộc** vào framework bên ngoài. Router gọi Use Case; Use Case phụ thuộc vào `Protocol` port; Adapter triển khai port. Trong Python, các package chỉ import code ổn định ở tầng trong, không import ngược lên tầng presentation hay infrastructure.

**AR-003 — Gắn kết theo chức năng (Functional Cohesion):** Tổ chức mã nguồn theo *tính năng/nghiệp vụ (feature/domain)* trước, sau đó mới chia layer bên trong feature. Các technical adapter dùng chung thuộc về explicit platform packages; tránh sao chép lặp lại client HTTP/DB/model-gateway ở từng tính năng.

**AR-004 — Ranh giới ổn định (Stable Boundaries):** `control-api` và `agent-runtime` là hai tiến trình độc lập. Không import chéo các Python module nội bộ hay ORM models của nhau. Giao tiếp bắt buộc qua hợp đồng có phiên bản và kênh truyền được xác thực.

**AR-005 — Cổng Công cụ Duy nhất (Single Tool Gateway):** Mọi thao tác công cụ (`builtin`, `http`, `mcp`) đều phải tuân thủ nghiêm ngặt **12-Step ToolDispatcher** được quy định trong Coding Agent Rules v1; nghiêm cấm gọi tắt (bypass) từ node của workflow, loader của Skill, giao diện UI hay LLM callbacks.

**AR-006 — Nguồn chân lý duy nhất cho Hợp đồng (Single Source of Contracts):** Cấu trúc dữ liệu API, Event, và Spec bắt nguồn từ thư mục `contracts/`. Dữ liệu được xác thực ngay tại cổng vào (ingress) để sinh ra các typed DTOs. Tránh viết giao diện API thủ công song song khi đã có code generation.

**AR-007 — Tính thuần khiết của Domain (Domain Purity):** Các module Domain chỉ chứa entities, value objects, business invariants và domain errors. **TUYỆT ĐỐI KHÔNG ĐƯỢC IMPORT** `fastapi`, `starlette`, `sqlalchemy`, `alembic`, `langgraph`, `httpx`, `requests`, `redis`, `qdrant_client`, cloud SDKs hay secrets APIs vào domain code.

**AR-008 — Cổng giao tiếp tường minh (Explicit Ports):** Một port thể hiện nhu cầu nghiệp vụ của ứng dụng, không phải bề mặt SDK của nhà cung cấp (ví dụ: `AgentVersionRepository.get_published(...)`, không phải `SQLAlchemySession.execute(...)`). Tầng ứng dụng chỉ phụ thuộc vào ports và kiểu domain thuần túy.

**AR-009 — Điểm ghép nối cấu hình (Composition Root):** Chỉ liên kết (wire) các dependency cụ thể tại thời điểm khởi động ứng dụng (`main.py`, DI container, bootstrap worker). Không khởi tạo infrastructure clients bên trong domain entities hay use cases.

**AR-010 — Cấm Import Vòng (No Cyclic Imports):** Các vòng lặp import giữa các feature hoặc package là **lỗi chặn merge (merge blocker)**. Nếu có sự phụ thuộc lẫn nhau, phải trích xuất các abstraction ổn định dùng chung ra một contracts/domain package độc lập.

**AR-011 — Độ bền bỉ và Idempotency (Durable Persistence):** Tách bạch rõ 5 loại trạng thái (Agent/Workflow definitions, Checkpoints, Messages, Long-term memory, Enterprise Knowledge). Mọi tác vụ có side-effect bắt buộc sử dụng khóa chống trùng lặp `UNIQUE (tenant_id, workspace_id, operation_key)`.

**AR-012 — Không có Secret Dạng Plaintext (Zero Plaintext Secrets):** Mọi thông tin nhạy cảm (API keys, credentials, tokens) phải được quản lý dưới dạng `secret_ref` và chỉ được giải mã server-side trong transaction có audit trail.

**AR-013 — Snapshot Bất biến (Immutable Snapshots):** Mọi phiên bản phát hành Agent, Skill, Tool, Workflow khi đã publish là bất biến, gắn kèm `bundle_sha256` và được bảo vệ bằng trigger CSDL. Môi trường promote/rollback chỉ thay đổi con trỏ (`agent_publications`).

**AR-014 — Thẩm quyền Con người (Human Authority):** Các hành động rủi ro cao hoặc có side-effect bắt buộc phải có sự phê duyệt của con người (HITL) gắn với exact action hash; mô hình LLM tuyệt đối không thể tự duyệt hành động.

#### Ví dụ chuẩn — Pure Use Case với Port Abstraction:
```python
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PublishCommand:
    workspace_id: UUID
    agent_id: UUID
    expected_revision: int


class AgentPublisher(Protocol):
    async def publish(self, command: PublishCommand) -> UUID: ...


async def publish_agent(
    command: PublishCommand,
    publisher: AgentPublisher,
) -> UUID:
    # Use case thuần túy: phụ thuộc vào Port, không import Framework hay ORM
    if command.expected_revision < 0:
        raise ValueError("expected_revision must be non-negative")
    return await publisher.publish(command)
```

---

## 2. Hợp đồng Dữ liệu, API & Sự kiện — CTR-001 đến CTR-012

**CTR-001 — Định tuyến Chuẩn:** Tiền tố API là `/api/v1`; tài nguyên dùng danh từ số nhiều dạng `kebab-case` (ví dụ: `/workspaces/{workspace_id}/agents`). Tuân thủ đúng routes đã được định nghĩa trong các tài liệu SPEC.

**CTR-002 — Cấu trúc Lỗi Chuẩn RFC 9457:** Phản hồi lỗi phải có định dạng nhất quán với machine-readable `code`, thông báo an toàn `message`, `requestId`, `traceId`. Sử dụng đúng mã trạng thái HTTP (`400`/`422`, `401`, `403`, masked `404`, `409`/`412`, `429`, `503`).

**CTR-003 — Optimistic Concurrency & Idempotency:** Các thao tác sửa đổi dữ liệu (mutation) phải sử dụng concurrency control (`If-Match`, version/revision). Các request POST publish hoặc execute bắt buộc có `Idempotency-Key` được lưu trữ bền vững.

**CTR-004 — Phân trang Có Bounded Limit:** Bắt buộc giới hạn kích thước trang (bounded page size), sử dụng opaque cursor với thứ tự sắp xếp ổn định; tuyệt đối không trả về danh sách lịch sử vô hạn trong một response.

**CTR-005 — Kiểm thử Tính Tương thích Hợp đồng:** Tính tương thích của API được xác minh tự động bằng consumer test fixtures và quá trình compile client sinh tự động. Cùng một `schemaVersion` tuyệt đối không được âm thầm thay đổi ngữ nghĩa.

**CTR-006 — Đặc tả Sự kiện Bất biến (Event Spec):** Tên sự kiện có định dạng `noun.past_tense.v1` (ví dụ: `agent.published.v1`), mang version cố định, event ID, UTC timestamp, producer, correlation ID, phạm vi tenant/workspace, aggregate ID/version và payload. Không mang raw prompts, secrets, PII hay toàn bộ database row.

**CTR-007 — Transactional Outbox Pattern:** Mọi thay đổi dữ liệu kèm phát sự kiện phải được ghi vào outbox table trong cùng database transaction; consumers xử lý sự kiện idempotent với cơ chế deduplication bền vững.

**CTR-008 — Xác thực Hai Đầu (Validation Ingress/Egress):** Xác thực payload sự kiện cả lúc nhận và trước khi lưu trữ, không chỉ dựa vào nơi phát. Sự kiện lỗi phải được chuyển vào Dead Letter Queue (DLQ).

**CTR-009 — Chuẩn hóa Serialization JSON:** Sử dụng canonical JSON serialization để tạo digest checksum và ghim phiên bản; từ chối duplicate keys khi parse dữ liệu JSON nhạy cảm.

**CTR-010 — Giới hạn Tài nguyên (Resource Limits):** Mọi request, tool execution và model call bắt buộc có giới hạn kích thước (bytes, token budget, timeouts) và rate limit gắn với principal/tenant/workspace.

**CTR-011 — Chuẩn Định dạng Thời gian & Định danh:** Sử dụng UTC ISO-8601 cho timestamps; ID là UUID hoặc chuỗi opaque; cursor token không được để lộ cấu trúc dữ liệu hay secrets.

**CTR-012 — Báo cáo Tương thích Tự động:** CI phải chạy báo cáo tương thích OpenAPI/JSON Schema tự động và kiểm thử hai chiều giữa TypeScript client và Python Pydantic models.

---

## 3. Tiêu chuẩn Cơ sở Dữ liệu & Persistence — DB-001 đến DB-013

**DB-001 — Mục tiêu 56 Bảng Chuẩn hóa (DB-56 Baseline):** Baseline Day-1 gồm đúng **56 bảng domain / 7 schemas** (`iam=5, catalog=16, knowledge=7, runtime=12, integration=6, governance=4, ops=6`). Không tự ý tạo hoặc xóa bảng nếu chưa có ADR và migration được duyệt.

**DB-002 — Phân định Sở hữu Schema:** Mỗi schema sở hữu các bảng của nó; các module khác chỉ tương tác qua use-case boundary. Cross-module SQL joins bắt buộc phải thông qua read-model hoặc reporting contract được review riêng.

**DB-003 — Khóa Phức hợp & Khóa Ngoại Ràng buộc:** Sử dụng UUID; các bảng thuộc tenant phải mang khóa phức hợp `(tenant_id, workspace_id, ...)`. Cơ sở dữ liệu là ranh giới toàn vẹn dữ liệu cuối cùng (FK, CHECK, UNIQUE, RLS).

**DB-004 — Thực thi Force Row Level Security (RLS):** Bật `FORCE ROW LEVEL SECURITY` trên toàn bộ 55 bảng tenant-scoped. Đảm bảo ứng dụng không bypass RLS; thiết lập ngữ cảnh bằng `SET LOCAL` trong transaction và có kiểm thử negative test chống rò rỉ dữ liệu cross-tenant/cross-workspace.

**DB-005 — Migrations Tuần tự & Bất biến:** Migration được lưu trữ trong các file tuần tự bất biến (`0001_baseline.sql` sau đó là các bản nâng cấp). Áp dụng quy trình expand–migrate–contract cho breaking changes; luôn kiểm tra migration trên bản sao CSDL thật.

**DB-006 — Chỉ mục Phục vụ Tải Thực tế:** Đánh chỉ mục dựa trên truy vấn thực tế; loại bỏ các index trùng lặp với PK/UNIQUE; kiểm chứng bằng `EXPLAIN (ANALYZE, BUFFERS)`. Không tạo index đầu cơ tràn lan.

**DB-007 — AsyncSession Không Chia sẻ:** Không chia sẻ cùng một SQLAlchemy `AsyncSession` giữa các asyncio tasks. Giữ thời gian transaction ngắn nhất có thể; tuyệt đối không giữ lock DB trong lúc chờ model LLM, MCP network call hoặc chờ duyệt HITL.

**DB-008 — Trạng thái Tool Operation Có Reconcile:** Mọi chuyển trạng thái của Run/Approval/ToolOperation phải lưu nguyên tử kèm outbox event. Bảng `runtime.tool_operations` hỗ trợ trạng thái `unknown`; không bao giờ retry mù quáng các tác vụ ghi ngoài chưa rõ kết quả.

**DB-009 — Qdrant là Index Phái sinh:** Qdrant chỉ là vector retrieval index, không phải nguồn cấp quyền. Bắt buộc gắn filter `tenant_id` và `workspace_id` trên mọi truy vấn vector; lên kế hoạch re-index xác định từ PostgreSQL/Object Storage.

**DB-010 — Chống N+1 và Giới hạn Truy vấn:** Mọi truy vấn danh sách, approval queue hay run monitor đều phải bounded; chủ động eager load/batch; chỉ `SELECT` các cột cần thiết.

**DB-011 — Mã hóa & Xóa Dữ liệu Định kỳ:** Mã hóa dữ liệu nhạy cảm at-rest và in-transit. Logs và test fixtures không được chứa PII hay secrets; áp dụng chính sách lưu giữ và xóa dữ liệu (retention policies) rõ ràng.

**DB-012 — Kiểm chứng Phục hồi Thảm họa (DR):** Bản sao lưu phải được kiểm chứng bằng kịch bản khôi phục thực tế. Một PR migration không thể merge chỉ vì lệnh `upgrade` chạy qua mà không kiểm tra RLS và tính toàn vẹn.

**DB-013 — Luôn Dùng Parameter Binding:** Tuyệt đối cấm nối chuỗi SQL hoặc dùng f-string với dữ liệu người dùng. Tên bảng và tên cột chỉ được chọn từ allowlist hằng số do mã nguồn kiểm soát.

---

## 4. AI Runtime, Skill Engine, MCP & Tools — AI-001 đến AI-012

**AI-001 — Snapshot Phiên bản Bất biến:** Các phiên bản Agent khi phát hành phải bất biến, có canonical SHA-256 và khóa chặt toàn bộ version của dependencies (Tools, Skills, Workflows, Model Configs).

**AI-002 — Cô lập Engine LangGraph:** LangGraph được giới hạn bên trong tầng engine adapter; trạng thái runtime và checkpoint tuân thủ nghiêm ngặt [`SPEC-RUNTIME-001.md`](../04-module-specs/runtime/SPEC-RUNTIME-001.md).

**AI-003 — Dữ liệu Mô hình là Untrusted:** Phản hồi từ LLM, hướng dẫn từ Skill, nội dung từ Knowledge Base và mô tả Tool từ MCP đều là **dữ liệu không tin cậy**. Chúng chỉ có thể đề xuất hành động, không có quyền cấp phép.

**AI-004 — Bắt buộc Qua Tool Gateway:** Toàn bộ công cụ phải chạy qua Tool Gateway, kiểm tra Policy, CAS Approval Hash và Idempotency Reservation. Hủy tác vụ ngay lập tức nếu thiếu quyền, sai schema hoặc hash bị thay đổi.

**AI-005 — Nạp Skill Theo Nhu cầu (Progressive Disclosure):** Skill chỉ công khai metadata tối thiểu khi khởi tạo; nạp nội dung `SKILL.md` và tài nguyên on-demand khi LLM cần; giải phóng context sau khi thực thi để bảo toàn context window. MVP cấm thực thi script tùy ý từ Skill cho đến khi có sandbox ADR.

**AI-006 — Kiểm soát Giao thức MCP Chặt chẽ:** Đăng ký và phát hiện tool từ MCP Server không đồng nghĩa với việc cấp quyền. Tool mapping và schema hash của MCP phải được ghim phiên bản và kiểm tra trôi dạt (drift) trước khi gọi.

**AI-007 — Phân loại Tác động Phụ (Side Effects):** Không thực hiện bất kỳ tác động ghi ngoài nào nếu yêu cầu người dùng là read-only. Tác vụ ghi có rủi ro cao bắt buộc phải có sự phê duyệt của con người với exact request hash.

**AI-008 — Giới hạn Ngân sách Thực thi:** Mọi vòng lặp lập kế hoạch (planning loop) và lượt gọi tool phải có giới hạn bước (`max_steps`), giới hạn thời gian (timeout), ngân sách token/chi phí và cơ chế hủy bỏ (cancellation).

**AI-009 — Khôi phục An toàn (Reconciliation over Replay):** Khi resume hoặc replay một luồng chạy bị gián đoạn, không gọi lại các tác vụ ghi ngoài đã cam kết; kiểm tra biên nhận (receipt) trong bảng outbox trước khi xử lý tiếp.

**AI-010 — Kiểm tra Hợp đồng Hai Đầu Phản hồi:** Đầu ra của tool và phản hồi của model đều phải được validate theo schema tại ranh giới. Lỗi JSON parse hoặc dữ liệu thiếu phải báo lỗi rõ ràng, không coi chuỗi rỗng là thành công.

**AI-011 — Dấu vết Truy vết Tương quan:** Tracing OpenTelemetry phải gắn kèm version của Agent, Skill, Tool, MCP, Model và correlation IDs; tự động che giấu (redact) thông tin nhạy cảm.

**AI-012 — Bộ Đánh giá Tự động Bắt buộc:** Bộ test tự động phải kiểm tra các kịch bản: chọn nhầm Skill, schema MCP bị stale, prompt injection trong mô tả tool, bypass phê duyệt, rò rỉ dữ liệu đa tenant và gọi trùng lặp tool khi retry.

---

## 5. An toàn Bảo mật & Chuỗi Cung ứng — SEC-001 đến SEC-010

**SEC-001 — Threat Modeling cho Mọi Khả năng Mới:** Phân tích mô hình đe dọa trước khi tích hợp tính năng bên ngoài (MCP mới, import Skill, upload file, web search, model provider mới); rà soát ranh giới tin cậy trước khi code.

**SEC-002 — Xác thực Server-Side Cả Hai Tầng:** Xác thực danh tính và phân quyền cả tại API Gateway lẫn Tool Gateway; quyền hạn xuất phát từ server-side state, không suy diễn từ prompt hay role do client gửi.

**SEC-003 — Quản lý Bí mật bằng `secret_ref`:** Bí mật được lưu dưới dạng tham chiếu có phạm vi (scope) và thời hạn (TTL); giải mã trong bộ nhớ transaction và tự động che giấu trong logs, traces, screenshots, git history và browser bundles.

**SEC-004 — Kiểm soát Egress Chống SSRF Đa tầng:** Mọi kết nối mạng ra ngoài (MCP HTTP, Webhook, Fetch) phải qua bộ lọc SSRF 4 lớp: chặn loopback (127.0.0.1), mạng nội bộ (RFC 1918), link-local và cloud metadata service (`169.254.169.254`). Cấm người dùng hay LLM truyền URL tùy ý kèm credential nội bộ.

**SEC-005 — Bảo mật Chuỗi Cung ứng Mã nguồn:** Cài đặt package từ registry tin cậy; khóa chặt phiên bản trong `uv.lock` và `pnpm-lock.yaml`; rà quét lỗ hổng định kỳ; không nâng cấp major version tự động mà không kiểm tra tương thích.

**SEC-006 — Quét Tự động Trên CI/CD:** CI phải tự động quét mã nguồn tìm credentials lộ lọt, thư viện có lỗ hổng bảo mật đã biết và log patterns vi phạm PII. Chặn release đối với các lỗ hổng High/Critical chưa giải quyết.

**SEC-007 — Phòng chống Tấn công Tệp tin (Zip-Slip & Traversal):** Khi tải lên hoặc giải nén gói Skill/tài liệu, bắt buộc kiểm tra path an toàn: chặn đường dẫn tuyệt đối, chặn `..`, chặn symlink trỏ ra ngoài thư mục cho phép và kiểm tra dung lượng chống decompression bomb.

**SEC-008 — An toàn Trình duyệt & API:** Thiết lập đầy đủ security headers, HTTPS-only cookies, chính sách CSRF, CORS allowlist nghiêm ngặt (cấm wildcard `*` đi kèm credentials) và rate limiting.

**SEC-009 — Cấm Thực thi Mã Không Kiểm soát:** Nghiêm cấm dùng `eval`, `exec`, shell string từ input của người dùng, model hoặc Skill. Các công cụ script execution chỉ chạy trong sandbox cách ly chuyên biệt được cấp phép qua ADR.

**SEC-010 — Tuân thủ Tiêu chuẩn OWASP ASVS/LLM:** Thiết kế hệ thống đối chiếu với danh mục kiểm soát OWASP Top 10 for LLM Applications (Prompt Injection, Insecure Output Handling, Sensitive Information Disclosure).

---

# PHẦN II: KỸ THUẬT LẬP TRÌNH & PHONG CÁCH MÃ NGUỒN (MICRO CODING TECHNIQUES)

## 6. Quy ước Đặt tên (Naming Conventions) — CT-001 đến CT-010

**CT-001 · Tên diễn đạt ý định nghiệp vụ (MUST):** `agent_version_id` thay vì `id2`, `is_published` thay vì `flag`, `pending_approval_count` thay vì `cnt`. Cấm đặt tên vô nghĩa `data`, `tmp`, `item`, `obj` trong business logic; biến đếm vòng lặp ngắn chỉ được dùng `index` hoặc `i` khi rõ nghĩa.

**CT-002 · Quy ước Python (MUST):** `snake_case` cho hàm/biến/module; `PascalCase` cho class, exception; `UPPER_SNAKE_CASE` cho hằng số; tiền tố `_` cho phương thức nội bộ. Hàm hành động dùng động từ: `load_skill_manifest`, `reserve_operation`, `validate_publish_request`.

**CT-003 · Quy ước TypeScript (MUST):** `camelCase` cho biến/hàm; `PascalCase` cho types/interfaces/components; `UPPER_SNAKE_CASE` cho global constants; tên file tiện ích dùng `kebab-case`, components dùng `PascalCase.tsx`.

**CT-004 · Đặt tên Biến Boolean (MUST):** Bắt buộc có tiền tố `is_`, `has_`, `can_`, `should_` (Python) hoặc `is`, `has`, `can`, `should` (TS) để đọc được như một mệnh đề khẳng định. Tuyệt đối không dùng `status: bool` để biểu diễn trạng thái nhiều pha.

**CT-005 · Tên Biến Danh sách & Tập hợp (SHOULD):** Dùng danh từ số nhiều `agents`, `tool_versions`; hậu tố `*_by_id` nếu là từ điển (map); `*_ids` nếu chứa danh sách ID. Không đặt `users` cho biến có kiểu `dict[user_id, User]`.

**CT-006 · Gắn Đơn vị Đo lường Tường minh (MUST):** Bắt buộc có hậu tố đơn vị: `timeout_seconds`, `duration_ms`, `max_tokens`, `cost_usd`. Thời gian dùng UTC, phân biệt rõ `created_at` với `expires_at`. Cấm đặt `ttl = 30` mà không rõ giây hay phút.

**CT-007 · Phân biệt Rõ các Loại Định danh (MUST):** Tách bạch rõ `tenant_id`, `workspace_id`, `agent_id`, `agent_version_id`, `run_id`, `tool_operation_id`; không truyền nhầm dù chúng có cùng kiểu `UUID` hay `str`.

**CT-008 · Tên Hàm Async (SHOULD):** Dùng động từ tự nhiên (`fetch_agent`, `execute_tool`), không bắt buộc thêm hậu tố `_async` trừ khi tồn tại cả hai phiên bản đồng bộ và bất đồng bộ cạnh nhau.

**CT-009 · Tên Hàm Thể hiện Đúng Kết quả (MUST):** `try_parse_*` trả về `None`/Result khi lỗi; `require_*` sẽ raise exception nếu không tìm thấy; `get_*` không được ngầm tạo mới tài nguyên; `create_*` không được âm thầm ghi đè (upsert).

**CT-010 · Cấm Magic Numbers & Magic Strings (MUST):** Mọi giá trị số hay chuỗi có ý nghĩa nghiệp vụ phải được định nghĩa thành hằng số có tên rõ ràng hoặc lấy từ cấu hình đã validate.

#### So sánh Naming Thực tế:
```python
# ❌ KHÔNG NÊN (Tên tối nghĩa, magic numbers, boolean không rõ)
a = await repo.get(x)
if a.status == "1" and t > 30:
    await service.go(a)

# ✅ NÊN (Ý định rõ ràng, đơn vị đo lường tường minh)
agent_version = await repository.get_agent_version(agent_version_id)
if agent_version.is_published and elapsed_seconds > publish_timeout_seconds:
    await publisher.handle_expired_publish(agent_version)
```

---

## 7. Thiết kế Hàm, Method & Kiểm soát Luồng (Control Flow) — CT-011 đến CT-020

**CT-011 · Nguyên tắc Đơn Nhiệm (Single Clear Purpose — MUST):** Mỗi hàm chỉ làm **một việc chính**. Một hàm vừa truy vấn CSDL, vừa gọi LLM, vừa format HTTP response và vừa ghi audit log phải được tách thành các hàm theo đúng ranh giới trách nhiệm.

**CT-012 · Kích thước Hàm Tinh gọn (Small Cohesive Functions — HEURISTIC):** Hàm > 50 dòng hoặc có > 4 tham số nghiệp vụ là tín hiệu cần refactor; ưu tiên tính gắn kết (cohesion) hơn là ép tách thành các wrapper 1 dòng vô nghĩa.

**CT-013 · Kỹ thuật Guard Clauses (SHOULD):** Kiểm tra và xử lý sớm các trường hợp invalid/missing/denied ngay ở đầu hàm (early return), tránh lồng ghép `if-else` sâu quá 3 tầng.

**CT-014 · Kiểu Trả về Tường minh (Explicit Return — MUST):** Return type phải rõ ràng; không trả về kiểu hỗn hợp `bool | dict | str | None` tùy theo nhánh rẽ. Sử dụng typed Result object hoặc raise domain error có cấu trúc.

**CT-015 · Cấm Side Effects Ngầm (No Hidden Side Effects — MUST):** Hàm mang tên `validate_*` tuyệt đối không được ghi vào CSDL hoặc gọi HTTP. Các tác động thay đổi trạng thái (write, cache invalidate, publish) phải được thể hiện rõ trong tên hàm và docstring.

**CT-016 · Tách Biệt Tính toán Thuần túy Khỏi I/O (SHOULD):** Tách logic biến đổi dữ liệu thuần túy (pure computation) ra khỏi truy vấn mạng/CSDL để hỗ trợ kiểm thử đơn vị nhanh và mang tính tất định (deterministic).

**CT-017 · Tránh Bẫy Tham số Boolean (Avoid Boolean Traps — SHOULD):** Lời gọi `publish(agent, True, False)` gây khó hiểu; sử dụng keyword-only arguments, Enum hoặc Command Object (`publish(agent, is_dry_run=True, auto_approve=False)`).

**CT-018 · Chống Bùng nổ Trạng thái Boolean (MUST):** Sử dụng State Machine hoặc Enum thay cho việc kết hợp nhiều cờ boolean `is_done`, `is_failed`, `is_waiting` dễ dẫn đến trạng thái mâu thuẫn bất hợp lệ.

**CT-019 · Tránh Trừu tượng hóa Vội vã (No Premature Abstraction — MUST):** Không tạo `BaseManagerFactory` hay generic plugin chỉ phục vụ một use case duy nhất; chỉ tổng quát hóa khi có sự lặp lại thực tế từ 3 trường hợp trở lên.

**CT-020 · Tính Tất định của Logic Nghiệp vụ (SHOULD):** Logic băm canonical hash, format dữ liệu hay validate invariant không được phụ thuộc ngầm vào đồng hồ hệ thống (`datetime.now()`) hay số ngẫu nhiên; luôn inject Clock hoặc Seed từ bên ngoài.

#### Ví dụ chuẩn — Pure Calculation không có Side Effect:
```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolBudget:
    max_calls: int
    consumed_calls: int


def remaining_tool_calls(budget: ToolBudget) -> int:
    """Tính toán thuần túy: không side effect, không I/O, hoàn toàn tất định."""
    if budget.max_calls < 0 or budget.consumed_calls < 0:
        raise ValueError("Budget values must be non-negative")
    return max(0, budget.max_calls - budget.consumed_calls)
```

---

## 8. Chuẩn Python & Kỹ thuật Lập trình — PY-001..PY-016 & CT-021..CT-045

### 8.1. Nguyên tắc cốt lõi: PY-001 đến PY-016
- **PY-001 — Phiên bản Runtime:** Chuẩn sản xuất là **Python 3.12**. Khai báo `requires-python = ">=3.12,<3.14"` đồng bộ trên toàn bộ `pyproject.toml`.
- **PY-002 — Bố cục Package:** Sử dụng cấu trúc `src/` layout; khai báo package rõ ràng trong uv workspace; không dựa vào biến môi trường `PYTHONPATH`.
- **PY-003 — Quy ước Tên:** Module `snake_case`, Class `PascalCase`, Constant `UPPER_SNAKE_CASE`. Tránh tên vô nghĩa `helper.py`, `common.py`.
- **PY-004 — Khai báo Kiểu Đầy đủ:** Type annotations trên 100% public functions; cấm `Any` ngầm; ưu tiên `Protocol`, `TypedDict` và frozen dataclasses.
- **PY-005 — Linter & Formatter:** Bắt buộc vượt qua `ruff check` và `ruff format --check` với độ dài dòng chuẩn 100 ký tự, target `py312`.
- **PY-006 — Typecheck với Mypy Strict:** Toàn bộ ứng dụng và thư viện dùng chung phải pass `mypy --strict`. Cấm dùng `ignore_missing_imports = true` toàn cục.
- **PY-007 — Xác thực Ranh giới với Pydantic v2:** JSON Schema là nguồn chân lý; Pydantic model cài đặt với `extra="forbid"` để chặn các trường rác không mong muốn.
- **PY-008 — Phân định Ngoại lệ Nghiệp vụ:** Lớp Domain định nghĩa các exception thuần túy (`AgentNotPublishableError`); tầng API chịu trách nhiệm map sang HTTP status.
- **PY-009 — Kỷ luật Bất đồng bộ (Async):** Chỉ dùng `async def` khi thực sự có async I/O; cấm gọi thư viện sync I/O làm nghẽn event loop; luôn có timeout rõ ràng.
- **PY-010 — Vòng đời Transaction CSDL:** Một `AsyncSession` của SQLAlchemy trên một task/request scope; cấm chia sẻ session giữa các task song song; commit/rollback tường minh.
- **PY-011 — Độ chính xác Dữ liệu:** Sử dụng kiểu `Decimal` cho số tiền và chi phí token; sử dụng timezone-aware UTC datetime (`datetime.now(timezone.utc)`). Cấm dùng `float` cho tiền tệ.
- **PY-012 — Structured Logging:** Sử dụng `structlog` hoặc logging có cấu trúc kèm `trace_id`, `request_id`, `tenant_id`; tự động che giấu secrets và PII. Cấm dùng `print()`.
- **PY-013 — Quản lý Cấu hình:** Quản lý cấu hình qua Pydantic `BaseSettings` tại thời điểm bootstrap; bí mật giải mã qua `secret_ref`. Cấm commit file `.env`.
- **PY-014 — Thứ tự Import Chuẩn:** Standard library $\rightarrow$ Third-party packages $\rightarrow$ Local packages (tuân thủ Ruff `I`). Cấm import wildcard `from module import *`.
- **PY-015 — Khả năng Bảo trì:** Viết code đơn giản, giải thích lý do (*why*) trong comment; tài liệu hóa các bất biến quan trọng bằng docstrings và tests.
- **PY-016 — Quản lý Tài nguyên (Resource Lifecycle):** HTTP clients, connection pools, model clients bắt buộc quản lý qua context manager (`async with`) hoặc lifespan của FastAPI.

### 8.2. Typing, Dataclasses & Models: CT-021 đến CT-029
- **CT-021 (MUST):** Mọi hàm public phải có type annotations đầy đủ cho tham số và kiểu trả về.
- **CT-022 (MUST):** Giá trị có thể vắng mặt phải khai báo rõ `T | None`; kiểm tra trước khi dùng; không dùng `# type: ignore` để che nhánh rẽ.
- **CT-023 (SHOULD):** Sử dụng `typing.Protocol` để định nghĩa hợp đồng hành vi cho Adapter thay vì import trực tiếp class của vendor SDK.
- **CT-024 (SHOULD):** Sử dụng `@dataclass(frozen=True, slots=True)` cho immutable value objects trong domain; Pydantic dành cho dữ liệu đầu vào chưa tin cậy tại API boundary.
- **CT-025 (SHOULD):** Dùng `enum.StrEnum` cho các tập trạng thái hữu hạn đã được định nghĩa trong CSDL; cấm magic strings.
- **CT-026 (MUST):** Thiết kế kiểu dữ liệu khiến các trạng thái bất hợp lệ không thể biểu diễn được (Make illegal states unrepresentable).
- **CT-027 (SHOULD):** Cấm dùng mutable default arguments (`[]`, `{}`); sử dụng `default_factory` theo đúng chuẩn.
- **CT-028 (MUST):** Hạn chế tối đa `typing.cast()` và `# type: ignore`; nếu bắt buộc phải dùng, phải có comment giải thích lý do kỹ thuật cụ thể.
- **CT-029 (MUST):** Serialization phải tường minh; không giả định `dict[str, object]` là hợp lệ với JSON Schema nếu chưa qua validator.

#### Ví dụ chuẩn — Protocol & Immutable Domain Value Object:
```python
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PublishedAgent:
    id: UUID
    version_id: UUID
    bundle_sha256: str


class AgentReader(Protocol):
    async def get_published(self, *, agent_id: UUID) -> PublishedAgent | None: ...


async def require_published_agent(reader: AgentReader, agent_id: UUID) -> PublishedAgent:
    """Xác thực thực thể bắt buộc tồn tại; raise domain error nếu missing."""
    agent = await reader.get_published(agent_id=agent_id)
    if agent is None:
        raise LookupError(f"Published agent '{agent_id}' not found")
    return agent
```

### 8.3. Xử lý Lỗi & Logging Có Cấu trúc: CT-030 đến CT-038
- **CT-030 (MUST):** Chỉ bắt ngoại lệ cụ thể (Specific Exceptions). Tuyệt đối cấm `except Exception: pass`, bare `except:`, hoặc nuốt lỗi rồi trả về thành công.
- **CT-031 (MUST):** Giữ nguyên ngữ cảnh lỗi gốc khi re-raise bằng cú pháp `raise DomainError(...) from exc`.
- **CT-032 (MUST):** Không tạo fallback giả khi gặp lỗi phân quyền hoặc sai schema; cấm tự ý đổi trạng thái từ `unknown` thành `approved`.
- **CT-033 (MUST):** Chỉ retry với các lỗi mạng tạm thời hoặc timeout; phải có exponential backoff và jitter; không retry các tác vụ ghi ngoài thiếu idempotency.
- **CT-034 (MUST):** Mọi lời gọi dịch vụ ngoài bắt buộc có timeout rõ ràng; khi timeout xảy ra, trạng thái ghi nhận là `unknown` để chuẩn bị đối soát (reconcile).
- **CT-035 (MUST):** Không được nuốt `asyncio.CancelledError`; dọn dẹp tài nguyên trong `finally` và re-raise cancellation theo đúng chuẩn runtime.
- **CT-036 (MUST):** Logging có cấu trúc dạng JSON; luôn gắn correlation IDs (`request_id`, `run_id`, `tenant_id`); tuyệt đối cấm log mật khẩu, token, hoặc payload nhạy cảm.
- **CT-037 (SHOULD):** Sử dụng `logger.exception(...)` trong khối `except` khi cần ghi nhận stack trace; tránh log lặp lại ở nhiều tầng liên tiếp.
- **CT-038 (MUST):** Domain model không được raise `fastapi.HTTPException`. Tầng router chịu trách nhiệm bắt domain error và map sang chuẩn RFC 9457.

#### Ví dụ chuẩn — Bắt lỗi cụ thể & Giữ nguyên Ngữ cảnh Lỗi gốc:
```python
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class ProviderUnavailableError(Exception):
    """Ngoại lệ domain khi provider bên ngoài không phản hồi."""


class RemoteProvider(Protocol):
    async def fetch(self, *, resource_id: str) -> str: ...


async def read_remote_resource(provider: RemoteProvider, resource_id: str) -> str:
    try:
        return await provider.fetch(resource_id=resource_id)
    except TimeoutError as exc:
        logger.warning("provider_call_timeout", extra={"resource_id": resource_id})
        raise ProviderUnavailableError(f"Provider timed out on resource: {resource_id}") from exc
```

### 8.4. Lập trình Bất đồng bộ & Concurrency: CT-039 đến CT-045
- **CT-039 (SHOULD):** Chỉ dùng `async` cho các tác vụ I/O bất đồng bộ thật sự. Tác vụ nặng về CPU phải đẩy sang worker pool hoặc tiến trình riêng.
- **CT-040 (MUST):** Luôn `await` mọi coroutine; không để lọt unawaited coroutines; tác vụ chạy nền phải quản lý qua TaskGroup hoặc Supervisor.
- **CT-041 (MUST):** Giới hạn mức độ đồng thời (Bounded Concurrency) bằng `asyncio.Semaphore` hoặc Worker Queue; không spawn task không giới hạn theo dữ liệu đầu vào.
- **CT-042 (MUST):** Mỗi task chạy song song bắt buộc phải có một `AsyncSession` riêng biệt; không dùng chung session giữa các nhánh của `asyncio.gather`.
- **CT-043 (MUST):** Giữ thời gian transaction CSDL ngắn nhất; giải phóng row lock trước khi thực hiện cuộc gọi mạng I/O dài (LLM, MCP, HITL).
- **CT-044 (MUST):** Lan truyền ngữ cảnh (`request_id`, `tenant_id`, trace context) thông qua Task Envelope hoặc `contextvars`; không dùng global mutable state.
- **CT-045 (MUST):** Đảm bảo cơ chế Clean Shutdown; dọn dẹp kết nối và flush outbox receipts bền vững khi ứng dụng nhận tín hiệu `SIGTERM`.

#### Ví dụ chuẩn — Bounded Concurrency với Semaphore:
```python
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


async def bounded_map(
    items: Sequence[str],
    operation: Callable[[str], Awaitable[T]],
    *,
    max_concurrency: int = 4,
) -> list[T]:
    """Thực thi song song có kiểm soát số lượng tác vụ đồng thời tối đa."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    semaphore = asyncio.Semaphore(max_concurrency)

    async def worker(item: str) -> T:
        async with semaphore:
            return await operation(item)

    return await asyncio.gather(*(worker(item) for item in items))
```

---

## 9. Chuẩn TypeScript, React & Next.js — TS-001..TS-016 & CT-061..CT-078

### 9.1. Nguyên tắc cốt lõi: TS-001 đến TS-016
- **TS-001 — Compiler Strictness:** Bắt buộc bật `strict: true`, `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true`, `noFallthroughCasesInSwitch: true`.
- **TS-002 — Quy ước Tên:** Component/Types `PascalCase`, biến/hàm `camelCase`, constants `UPPER_SNAKE_CASE`, route segments `kebab-case`, hooks `useX`, handlers `handleX`.
- **TS-003 — Hợp đồng Dữ liệu:** Types của DTOs sinh trực tiếp từ OpenAPI/JSON Schema. Cấm dùng `any` hoặc ép kiểu `as unknown as T`.
- **TS-004 — Server Components First:** Mặc định là Server Component; chỉ thêm `"use client"` khi cần xử lý browser events, hooks hoặc client state.
- **TS-005 — Ranh giới Feature:** Mỗi tính năng có thư mục riêng (`components/`, `hooks/`, `api/`, `model/`, `validators/`); cấm import vòng giữa các features.
- **TS-006 — Truy cập API Tập trung:** Sử dụng một HTTP client chuẩn có cấu hình timeout, interceptor gắn correlation ID và cơ chế chuẩn hóa mã lỗi.
- **TS-007 — Bảo mật Server Actions:** Server Actions và Route Handlers là untrusted ingress; luôn xác thực danh tính, quyền hạn và CSRF phía máy chủ.
- **TS-008 — Xử lý Biểu mẫu (Forms):** Sử dụng các component biểu mẫu accessible và validate bằng Zod schema tương thích với backend contracts.
- **TS-009 — Giao diện Nền tảng:** Sử dụng Tailwind CSS kết hợp shadcn/ui và Radix UI primitives; đảm bảo độ tương phản màu sắc và hỗ trợ điều hướng bàn phím.
- **TS-010 — Quản lý State:** Trạng thái cục bộ dùng cho UI; dữ liệu máy chủ quản lý qua React Query/SWR với query key chứa `tenant_id` và `workspace_id`.
- **TS-011 — Xử lý Toàn diện Trạng thái Lỗi:** Xử lý đầy đủ: Loading, Empty, Unauthorized, Conflict, Pending Approval, Retryable Error và Terminal Failure.
- **TS-012 — Hiệu năng Hiển thị:** Phân trang và ảo hóa danh sách (virtualization) khi hiển thị danh sách dài hoặc transcript hội thoại LLM.
- **TS-013 — Khả năng Tiếp cận (Accessibility - a11y):** Đảm bảo nhãn accessible cho icon, quản lý focus đúng chuẩn trên Modal/Dialog và hỗ trợ phím Tab.
- **TS-014 — Kiểm thử Giao diện:** Viết unit/component test cho form validation, permission rendering và browser E2E test cho luồng publish/approval.
- **TS-015 — Chất lượng Code Frontend:** Bắt buộc pass ESLint (`next/core-web-vitals` + TypeScript rules) và format đồng nhất bằng Prettier.
- **TS-016 — Bảo mật Client-Side:** Tuyệt đối không để lộ token bí mật, API keys hay `secret_ref` trong bundle client; không lưu trữ credential trong localStorage nếu dùng cookie session.

### 9.2. TypeScript Types, Strictness & Error Handling: CT-061 đến CT-070
- **CT-061 (MUST):** Không sử dụng `any`; dữ liệu từ `JSON.parse`, `fetch`, localStorage có kiểu `unknown` trước khi qua validator.
- **CT-062 (SHOULD):** Sử dụng **Discriminated Unions** để mô hình hóa trạng thái UI và trạng thái Run thay cho việc dùng nhiều cờ boolean dễ mâu thuẫn.
- **CT-063 (SHOULD):** Sử dụng hàm kiểm tra `assertNever(x: never)` trong nhánh `default` của `switch-case` để đảm bảo xử lý đầy đủ mọi trường hợp (Exhaustive Checking).
- **CT-064 (SHOULD):** Giữ dữ liệu bất biến; sử dụng `Readonly<T>` cho props và state; không mutate trực tiếp mảng hoặc object.
- **CT-065 (MUST):** Tách biệt DTOs truyền tải mạng với View Model của giao diện; không định nghĩa lại interface API thủ công ở nhiều nơi.
- **CT-066 (MUST):** Bắt lỗi với `catch (error: unknown)`; thu hẹp kiểu an toàn (`error instanceof Error`) trước khi đọc thuộc tính.
- **CT-067 (MUST):** Sử dụng `AbortController` để hủy bỏ các network request khi component bị unmount hoặc người dùng đổi trang.
- **CT-068 (MUST):** Không để lọt unhandled promises trong event handlers; luôn có khối `try-catch` hoặc bắt lỗi rõ ràng.
- **CT-069 (MUST):** Phân biệt rõ ràng giữa rỗng, null và undefined; ưu tiên toán tử Nullish Coalescing `??` hơn Logical OR `||`.
- **CT-070 (MUST):** Cấm sao chép dữ liệu người dùng bừa bãi bằng toán tử spread `...userInput` vào request có đặc quyền; chỉ gán các trường trong allowlist.

#### Ví dụ chuẩn — Discriminated Unions & Exhaustive Checking:
```typescript
// ✅ Mô hình trạng thái bất biến, không thể vừa loading vừa error
type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved"; versionId: string }
  | { status: "error"; message: string };

function assertNever(value: never): never {
  throw new Error(`Unhandled union member: ${JSON.stringify(value)}`);
}

function describeSaveState(state: SaveState): string {
  switch (state.status) {
    case "idle":
      return "Chưa bắt đầu";
    case "saving":
      return "Đang lưu...";
    case "saved":
      return `Đã lưu thành công phiên bản ${state.versionId}`;
    case "error":
      return `Lỗi: ${state.message}`;
    default:
      return assertNever(state);
  }
}

// ✅ Thu hẹp kiểu lỗi an toàn
function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Đã xảy ra lỗi không xác định";
}
```

### 9.3. Kiến trúc Next.js App Router & React Components: CT-071 đến CT-078
- **CT-071 (SHOULD):** Kiến trúc Server-First; giữ ranh giới `"use client"` nhỏ nhất có thể (chỉ bao bọc các nút bấm hoặc form tương tác).
- **CT-072 (MUST):** Ngăn chặn việc import nhầm code server vào client bằng package `server-only`. Tuyệt đối không để lộ biến môi trường bí mật lên trình duyệt.
- **CT-073 (MUST):** Chiến lược tải dữ liệu có chủ đích; Server Component gọi trực tiếp dịch vụ dữ liệu nội bộ thay vì tự gọi vòng qua HTTP Route Handler của chính nó.
- **CT-074 (MUST):** Tuân thủ nghiêm ngặt Rules of Hooks; khai báo đầy đủ dependency array trong `useEffect`; không lạm dụng effect để tính toán dữ liệu phái sinh.
- **CT-075 (MUST):** Thuộc tính `key` trong danh sách phải là ID ổn định duy nhất của bản ghi; cấm dùng mảng `index` cho danh sách có thể sắp xếp hoặc xóa.
- **CT-076 (MUST):** Sử dụng các thẻ HTML ngữ nghĩa (`<button>` cho hành động, `<a>` cho chuyển trang, `<label>` gắn với input form).
- **CT-077 (MUST):** Vô hiệu hóa nút bấm và hiển thị trạng thái đang xử lý khi submit form để chống double-click tạo trùng lặp request.
- **CT-078 (HEURISTIC):** Tách nhỏ component khi có quá nhiều logic; tách custom hook để quản lý state phức tạp.

#### Ví dụ chuẩn — Client Component An toàn với Chống Double-Submit:
```tsx
"use client";

import { useState } from "react";

type PublishButtonProps = Readonly<{
  onPublish: () => Promise<void>;
}>;

export function PublishButton({ onPublish }: PublishButtonProps) {
  const [isPending, setIsPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handlePublish(): Promise<void> {
    if (isPending) return;
    setIsPending(true);
    setErrorMessage(null);
    try {
      await onPublish();
    } catch {
      setErrorMessage("Không thể xuất bản tác tử. Vui lòng thử lại sau.");
    } finally {
      setIsPending(false);
    }
  }

  return (
    <div>
      <button
        type="button"
        disabled={isPending}
        onClick={() => { void handlePublish(); }}
        className="px-4 py-2 bg-blue-600 text-white rounded disabled:opacity-50"
      >
        {isPending ? "Đang xuất bản..." : "Xuất bản Phiên bản"}
      </button>
      {errorMessage && <p role="alert" className="text-red-500 mt-2">{errorMessage}</p>}
    </div>
  );
}
```

---

## 10. Kỹ thuật Lập trình Persistence & SQL — CT-046 đến CT-053

**CT-046 · Parameter Binding 100% (MUST):** Cấm nối chuỗi SQL hoặc dùng f-string với dữ liệu người dùng. Mọi giá trị tham số phải truyền qua Parameter Binding của SQLAlchemy. Tên bảng và cột chỉ được lấy từ hằng số code kiểm soát.

**CT-047 · Lựa chọn Cột Tường minh (SHOULD):** Chỉ truy vấn đúng các cột cần thiết; tránh dùng `SELECT *` trong các hot path; không gọi `.all()` trên các bảng lớn mà không có giới hạn phân trang.

**CT-048 · Tránh Vấn nạn N+1 (MUST):** Khi truy vấn danh sách đi kèm quan hệ (relations), bắt buộc dùng eager loading (`selectinload`, `joinedload`) hoặc truy vấn batch.

**CT-049 · Thay đổi Trạng thái Nguyên tử (MUST):** Sử dụng Database Constraints, Transaction và Optimistic Concurrency Control (CAS). Tuyệt đối không đọc dữ liệu ra rồi kiểm tra bằng mã ứng dụng mà không có khóa khi cần đảm bảo tính duy nhất.

**CT-050 · Tenant Isolation ở Cả Ứng dụng Lẫn CSDL (MUST):** Mọi truy vấn bắt buộc phải lọc theo `tenant_id` và `workspace_id` trong câu lệnh SQL; cơ chế RLS ở tầng CSDL là lớp bảo vệ chiều sâu thứ hai.

**CT-051 · Sắp xếp Ổn định & Phân trang Bounded (MUST):** Sắp xếp danh sách bắt buộc có tiebreaker (`ORDER BY created_at DESC, id DESC`); giới hạn `limit` tối đa; ưu tiên keyset/cursor pagination cho bảng dữ liệu lớn.

**CT-052 · Tôn trọng Ràng buộc CSDL (MUST):** Không xử lý trùng lặp chỉ bằng cách `SELECT` kiểm tra trước rồi mới `INSERT`; hãy để ràng buộc `UNIQUE` của CSDL làm thẩm quyền cao nhất và bắt ngoại lệ `IntegrityError` để xử lý.

**CT-053 · Đánh Chỉ mục Theo Tải Thực tế (SHOULD):** Kiểm tra kế hoạch thực thi bằng `EXPLAIN (ANALYZE, BUFFERS)`; không đánh lặp lại index đã có trên Primary Key hoặc Unique Key.

#### Ví dụ chuẩn — Truy vấn SQLAlchemy với Khóa Phức hợp Tenant:
```python
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def find_agent_version(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    agent_version_id: UUID,
) -> object | None:
    # Truy vấn có scoping tường minh tenant_id và workspace_id
    statement = (
        select(AgentVersionModel)
        .where(AgentVersionModel.tenant_id == tenant_id)
        .where(AgentVersionModel.workspace_id == workspace_id)
        .where(AgentVersionModel.id == agent_version_id)
    )
    return await session.scalar(statement)
```

---

## 11. Kỹ thuật Lập trình FastAPI, Validation & HTTP — CT-054 đến CT-060

**CT-054 · Handlers Tinh gọn (Thin Handlers — MUST):** Router chỉ làm nhiệm vụ parse request, kiểm tra xác thực, gọi Application Use Case, map mã lỗi và trả về response; không chứa logic nghiệp vụ phức tạp, câu lệnh SQL hay trực tiếp gọi tool execution.

**CT-055 · Schema-First tại Cổng vào và Cổng ra (MUST):** DTOs của request và response phải khớp 100% với đặc tả OpenAPI và JSON Schema có phiên bản; response model không bao giờ để rò rỉ thuộc tính nội bộ của ORM hay các giá trị secrets.

**CT-056 · Phân biệt Validation Cú pháp và Phân quyền (MUST):** Pydantic kiểm tra tính hợp lệ về kiểu và khoảng giá trị (syntax validation); phân quyền (authorization) bắt buộc phải kiểm tra server-side dựa trên Identity Principal và Workspace context.

**CT-057 · Ngữ nghĩa Mã Trạng thái HTTP (MUST):**
- `400` / `422`: Dữ liệu đầu vào không hợp lệ về mặt cú pháp hoặc schema.
- `401`: Chưa xác thực danh tính.
- `403`: Không có quyền truy cập vào tài nguyên.
- `404`: Không tìm thấy tài nguyên (áp dụng chính sách che giấu tồn tại nếu cần).
- `409`: Xung đột phiên bản (optimistic concurrency conflict) hoặc trùng lặp idempotency key.
- `429`: Vượt quá hạn ngạch (rate limit / quota exceeded).
- `5xx`: Lỗi hệ thống nội bộ; ẩn toàn bộ stack trace khỏi response trả về cho client.

**CT-058 · Giới hạn Dung lượng Request (MUST):** Đặt giới hạn trần cho payload size, độ dài chuỗi, số lượng phần tử mảng và độ sâu JSON để ngăn chặn tấn công DoS.

**CT-059 · Dependency Injection Tường minh (SHOULD):** Sử dụng `fastapi.Depends` để inject các service và use case đã được cấu hình; tránh dùng biến global singleton mutable.

**CT-060 · Tài liệu hóa Đi kèm Hợp đồng (MUST):** Mọi endpoint mới phải có ví dụ request/response trong OpenAPI spec; mọi thay đổi breaking change phải trải qua quy trình đánh giá tương thích ngược.

---

## 12. Pattern Lập trình Agent Runtime, Skills & MCP — CT-079 đến CT-086

**CT-079 · Đầu ra LLM là Dữ liệu Chưa Tin cậy (MUST):** Parse đầu ra của mô hình về typed schema và validate trước khi đưa vào quyết định tiếp theo; tuyệt đối cấm đưa văn bản của model vào hàm `eval()`, câu lệnh SQL, lệnh shell hay HTTP request trực tiếp.

**CT-080 · Chuyển trạng thái Thuần túy (Pure State Transitions — SHOULD):** Hàm quyết định bước tiếp theo của Agent nhận typed State + Event và trả về State mới; tách biệt hoàn toàn logic ra quyết định khỏi các tác vụ I/O mạng.

**CT-081 · Cố định Ngân sách Thực thi (Fixed Budgets — MUST):** Mọi luồng lặp của tác tử bắt buộc phải có chặn trên về số bước (`max_steps`), thời gian tối đa (`timeout`), ngân sách token và điểm kiểm tra hủy bỏ (`cancellation_token`).

**CT-082 · Tool Gateway là Con đường Duy nhất (MUST):** Hướng dẫn từ Skill và công cụ từ MCP không tự tạo ra quyền hạn; việc thực thi công cụ bắt buộc phải đi qua 12-Step ToolDispatcher tại [`SPEC-TOOL-001.md`](../04-module-specs/tool/SPEC-TOOL-001.md).

**CT-083 · Ghim Chặt Phiên bản Tham chiếu (Pinned References — MUST):** Một luồng chạy (Run) đang thực thi chỉ được trỏ tới các phiên bản Agent, Skill, Tool, Model đã được ghim phiên bản cố định (immutable version ID); cấm tự ý resolve sang bản "latest" khi đang chạy.

**CT-084 · Phân định Rõ Checkpoint và Memory (MUST):** Checkpoint phục vụ việc khôi phục trạng thái luồng chạy (thread recovery); Long-term Memory là tri thức lâu dài có sự đồng thuận của người dùng và chính sách TTL riêng; không trộn lẫn dữ liệu checkpoint vào memory.

**CT-085 · Idempotency Đi kèm Đối soát (Reconciliation — MUST):** Khóa duy nhất `(tenant_id, workspace_id, operation_key)` là bước giữ chỗ nguyên tử phía CSDL. Khi gặp sự cố mạng với hệ thống ngoài, trạng thái phải ghi nhận là `unknown` để thực hiện đối soát trước khi quyết định retry.

**CT-086 · Phê duyệt Ràng buộc với Hành động Cụ thể (MUST):** Kiểm tra phê duyệt HITL phải khớp chính xác với Principal, Run ID, Tool Version và Action Hash của payload; model LLM tuyệt đối không thể tự sinh ra cờ `is_approved: true`.

#### Ví dụ chuẩn — Pure State Machine Transition cho Agent:
```python
from dataclasses import dataclass
from enum import StrEnum


class RunPhase(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    phase: RunPhase
    remaining_steps: int


def transition_to_next_step(snapshot: RunSnapshot) -> RunSnapshot:
    # Chuyển trạng thái tất định: kiểm tra invariant, trừ bước, không side-effect
    if snapshot.phase is not RunPhase.PLANNED and snapshot.phase is not RunPhase.RUNNING:
        raise ValueError(f"Cannot progress run in phase '{snapshot.phase}'")
    if snapshot.remaining_steps <= 0:
        raise ValueError("Budget exhausted: no remaining steps allowed")
    return RunSnapshot(phase=RunPhase.RUNNING, remaining_steps=snapshot.remaining_steps - 1)
```

---

# PHẦN III: ĐẢM BẢO CHẤT LƯỢNG, CÔNG CỤ & QUY TRÌNH PULL REQUEST

## 13. Chiến lược & Kỹ thuật Kiểm thử — QA-001..QA-014 & CT-087..CT-096

### 13.1. Merge Gates & Kim tự tháp Kiểm thử: QA-001 đến QA-014
- **QA-001 — Kim tự tháp Kiểm thử:** Tập trung nhiều nhất vào Unit/Contract tests; tiếp theo là Integration tests; đỉnh tháp là E2E workflow tests chọn lọc.
- **QA-002 — Báo cáo Kết quả Trung thực:** Bắt buộc báo cáo 4 trạng thái: `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED` đi kèm lệnh chạy cụ thể.
- **QA-003 — Xác thực Schema & Hợp đồng:** Mọi thay đổi JSON Schema phải chạy test kiểm tra tương thích ngược và validate dữ liệu fixtures.
- **QA-004 — Rà soát RLS Cơ sở Dữ liệu:** Mọi thay đổi bảng hoặc quyền phải có negative test kiểm tra việc chặn truy cập trái phép cross-tenant.
- **QA-005 — Kiểm thử Cạnh tranh Idempotency:** Kiểm tra kịch bản 2 worker cùng chạy một `operation_key`; chỉ 1 worker được cấp phép, worker còn lại nhận conflict.
- **QA-006 — Mocking Đúng Ranh giới:** Mock ở tầng Protocol/Port ngoại vi; không mock logic nghiệp vụ đang cần kiểm tra.
- **QA-007 — Bền bỉ Trước Sự cố (Crash Recovery):** Kiểm thử kịch bản ngắt tiến trình giữa chừng và khôi phục lại trạng thái thành công từ checkpoint CSDL.
- **QA-008 — Kiểm thử Quy trình Phê duyệt HITL:** Kiểm tra luồng `interrupt()` khi gặp action rủi ro cao và `resume()` sau khi có token phê duyệt hợp lệ.
- **QA-009 — Kiểm thử Lá chắn Prompt Injection:** Xác minh bộ lọc Zero-Width và AST sandbox chặn đứng các nỗ lực vượt quyền từ prompt.
- **QA-010 — Kiểm tra Bộ lọc PII Tiếng Việt:** Xác minh việc xóa hoặc che giấu Số CMND/CCCD, Số điện thoại, Email theo Nghị định 13/2023/NĐ-CP.
- **QA-011 — Đánh giá Năng lực Tác tử (Golden Datasets):** Chạy bộ benchmark đánh giá độ chính xác trong việc chọn tool và tuân thủ giới hạn ngân sách.
- **QA-012 — Kiểm thử Hiệu năng & Tải:** Kiểm tra thời gian phản hồi p95, khả năng giải phóng bộ nhớ và việc không để rò rỉ connection pool.
- **QA-013 — Mutation Testing:** Kiểm tra độ nhạy của bộ test bằng cách đưa vào các đột biến logic nhỏ; test phải fail khi có lỗi xuất hiện.
- **QA-014 — Merge Blockers:** Không merge bất kỳ PR nào nếu còn tồn tại test fail, typecheck error, hoặc lint violation trong các rule bắt buộc.

### 13.2. Kỹ thuật Viết Test Thực chiến: CT-087 đến CT-096
- **CT-087 (MUST):** Đặt tên test thể hiện rõ hành vi cần kiểm chứng (`test_publish_rejects_stale_revision`), không đặt tên chung chung `test_agent()`.
- **CT-088 (SHOULD):** Cấu trúc test theo mẫu **Arrange — Act — Assert (AAA)** rõ ràng, tách bạch giữa phần chuẩn bị, phần thực thi và phần kiểm tra kết quả.
- **CT-089 (SHOULD):** Sử dụng `@pytest.mark.parametrize` để bao quát các trường hợp biên: mảng rỗng, giá trị âm, vượt trần, ký tự đặc biệt.
- **CT-090 (MUST):** Giả lập (mock) các cuộc gọi I/O bên ngoài tại ranh giới port; tích hợp CSDL thật (hoặc test container) trong các bài test tích hợp.
- **CT-091 (MUST):** Test phải mang tính tất định (Deterministic); cố định đồng hồ (`freezegun` / inject clock) và random seed; tuyệt đối cấm dùng `time.sleep()` để chờ hệ thống.
- **CT-092 (MUST):** Kiểm tra ngoại lệ bằng `pytest.raises(SpecificError, match="...")`; kiểm tra việc rollback dữ liệu khi gặp lỗi.
- **CT-093 (MUST):** Sử dụng contract fixtures để test cả hai chiều tương thích tiến và tương thích lùi.
- **CT-094 (MUST):** Luôn có bài test bảo mật phủ định (Security Negative Tests): cố tình truy cập tài nguyên của tenant khác và assert nhận mã lỗi `403` hoặc `404`.
- **CT-095 (MUST):** Kiểm thử race condition trên CSDL: mô phỏng 2 tiến trình đồng thời đặt chỗ cùng một idempotency key.
- **CT-096 (SHOULD):** Tỉ lệ test coverage là tín hiệu tham khảo; coverage cao không thay thế cho các bài test kịch bản đe dọa và test biên.

#### Ví dụ chuẩn — Parametrized Test theo mẫu AAA:
```python
import pytest


@pytest.mark.parametrize(
    ("max_calls", "consumed", "expected"),
    [
        (10, 0, 10),
        (10, 4, 6),
        (10, 10, 0),
        (10, 15, 0),
    ],
)
def test_remaining_tool_calls_calculation(max_calls: int, consumed: int, expected: int) -> None:
    # Arrange
    budget = ToolBudget(max_calls=max_calls, consumed_calls=consumed)
    # Act
    remaining = remaining_tool_calls(budget)
    # Assert
    assert remaining == expected


def test_negative_budget_raises_value_error() -> None:
    # Arrange & Act & Assert
    with pytest.raises(ValueError, match="non-negative"):
        remaining_tool_calls(ToolBudget(max_calls=-5, consumed_calls=0))
```

---

## 14. Hiệu năng, An toàn & Bảng Antipatterns Chặn Merge — CT-097..CT-104

### 14.1. Performance & Safety: CT-097 đến CT-104
- **CT-097 (MUST):** Chỉ tối ưu hóa hiệu năng sau khi đã có số liệu đo lường cụ thể (profiling/traces); benchmark trước và sau để chứng minh hiệu quả.
- **CT-098 (MUST):** Mọi tác vụ phải có giới hạn (Bounded Work); không tải toàn bộ bảng CSDL hoặc toàn bộ văn bản lớn vào bộ nhớ RAM.
- **CT-099 (SHOULD):** Tránh vô tình tạo thuật toán độ phức tạp $O(n^2)$; sử dụng `set` hoặc `dict` để tra cứu theo khóa thay vì lặp lồng nhau.
- **CT-100 (SHOULD):** Sử dụng Streaming Response hoặc Keyset Pagination khi truyền tải danh sách lớn; luôn giải phóng con trỏ CSDL khi client ngắt kết nối.
- **CT-101 (MUST):** Tính toán thời gian và tiền tệ chính xác: sử dụng UTC timestamps và kiểu `Decimal` cho số tiền; cấm dùng `float` cho giao dịch tài chính.
- **CT-102 (MUST):** Áp dụng các nguyên tắc lập trình an toàn: cấm deserialization không an toàn, cấm chèn biến vào shell, khử khuẩn HTML chống XSS.
- **CT-103 (SHOULD):** Comment chỉ giải thích lý do (*why*), không nhắc lại cú pháp code.
- **CT-104 (MUST):** Giữ pull request tập trung vào đúng phạm vi; tách việc refactor mã nguồn thành PR riêng biệt với PR sửa đổi tính năng.

### 14.2. Bảng 12 Antipatterns Chặn Merge

| STT | Antipattern Nguy hiểm | Nguy cơ Rủi ro | Giải pháp Khắc phục Bắt buộc |
|---|---|---|---|
| 1 | `except Exception: pass` hoặc bare `except:` | Nuốt lỗi nghiêm trọng, hệ thống rơi vào trạng thái silent corruption | Bắt đúng exception cụ thể, ghi log có cấu trúc và re-raise |
| 2 | Lạm dụng `Any` hoặc `dict[str, Any]` khắp nơi | Mất hợp đồng dữ liệu, phát sinh lỗi runtime bất ngờ | Sử dụng Typed Models (Pydantic / Dataclass), validate tại ranh giới |
| 3 | Thực thi `eval()`, `exec()` hoặc shell string từ Model | RCE (Thực thi mã từ xa), chiếm quyền máy chủ | Cấm hoàn toàn; chỉ chạy tool qua Tool Gateway có sandbox |
| 4 | Dùng chung 1 `AsyncSession` cho nhiều task song song | Race condition, hỏng transaction CSDL của PostgreSQL | Khởi tạo mỗi async task một `AsyncSession` độc lập |
| 5 | Dùng f-string nối chuỗi SQL từ tham số người dùng | SQL Injection nghiêm trọng | Luôn dùng Parameter Binding và SQLAlchemy expressions |
| 6 | Trả về `{"success": True}` khi timeout write ngoài | Báo sai kết quả nghiệp vụ, mất tính nhất quán | Ghi nhận trạng thái `unknown` và kích hoạt luồng đối soát (reconcile) |
| 7 | Nhận cờ `isApproved: true` từ client hoặc model LLM | Leo thang đặc quyền (Privilege Escalation) | Xác thực chữ ký và Action Hash server-side qua bảng `approvals` |
| 8 | Gắn `"use client"` cho toàn bộ Next.js App Router | Bundle phình to, nguy cơ rò rỉ bí mật server sang browser | Thiết kế Server-First; chỉ dùng Client Component ở lá cây UI |
| 9 | Dùng `as any`, `!`, hoặc `# type: ignore` để qua CI | Che giấu lỗi type, vô hiệu hóa static analysis | Thu hẹp kiểu dữ liệu (narrowing) hoặc dùng Type Guards |
| 10 | Hardcode giá trị timeout/retry phân tán ở nhiều file | Bất nhất, khó cấu hình và vận hành production | Quản lý qua `BaseSettings` tập trung, có budget rõ ràng |
| 11 | Viết test chỉ kiểm tra status code 200 | Không chứng minh được tính đúng đắn của nghiệp vụ | Kiểm thử cả state mutation, bảo mật, và các trường hợp biên |
| 12 | Query CSDL không có limit, order và tenant filter | Suy giảm hiệu năng hệ thống, rò rỉ dữ liệu đa tenant | Bắt buộc có tenant filter, bounded pagination và stable sort |

---

## 15. Vận hành, Giám sát & Khả năng Quan sát — OPS-001 đến OPS-008

**OPS-001 — Distributed Tracing:** Tích hợp OpenTelemetry trên toàn bộ luồng xử lý; lan truyền `traceparent` qua các lời gọi giữa `control-api`, `agent-runtime`, và các MCP servers.

**OPS-002 — Metrics Đo lường Chuẩn:** Thu thập metrics Prometheus: thời gian phản hồi (histogram p50, p95, p99), số lượng request, số lượt gọi tool, số token tiêu thụ và chi phí LLM ước tính.

**OPS-003 — Logging Tập trung:** Toàn bộ log xuất ra định dạng JSON stdout; tự động làm sạch (redact) các trường nhạy cảm (`Authorization`, `api_key`, `password`, `ssn`).

**OPS-004 — WORM Audit Trails:** Bản ghi nhật ký kiểm toán (bảng `governance.audit_events`) được bảo vệ bằng trigger CSDL chống sửa đổi (Write Once, Read Many).

**OPS-005 — Health Probes Đầy đủ:** Khai báo đầy đủ các endpoint `/healthz/live` (liveness) và `/healthz/ready` (readiness kiểm tra kết nối PostgreSQL, Qdrant, MinIO).

**OPS-006 — Ngưỡng Cảnh báo & Circuit Breaker:** Thiết lập circuit breaker ngắt kết nối tự động khi các dịch vụ bên ngoài (LLM provider, MCP server) gặp sự cố kéo dài.

**OPS-007 — Xử lý Hàng đợi Chết (DLQ):** Các sự kiện outbox hoặc webhook thất bại sau số lần retry quy định phải được chuyển vào Dead Letter Queue để kỹ thuật viên tra cứu.

**OPS-008 — Kịch bản Khôi phục Thảm họa (Disaster Recovery):** Toàn bộ quy trình sao lưu PITR (Point-in-Time Recovery) của PostgreSQL phải được diễn tập định kỳ theo [`DISASTER_RECOVERY.md`](../08-operations/DISASTER_RECOVERY.md).

---

## 16. Cấu trúc Repository, Quyền Sở hữu & Quy ước File

### 16.1. Bố cục Thư mục Chuẩn (Repository Layout)
```text
ENTERPRISE_AGENT_PLATFORM/
├── AGENTS.md                                                # 12 nguyên tắc cốt lõi cho Coding Agents
├── docs/00-governance/CODING_STANDARDS_AND_ARCHITECTURE_RULES.md # BẢN QUY CHUẨN KỸ THUẬT TOÀN DIỆN NÀY
├── CONTRIBUTING.md                                          # Quy định đóng góp mã nguồn
├── SECURITY.md                                              # Chính sách bảo mật & báo cáo lỗ hổng
├── Makefile                                                 # Lệnh tự động hóa
├── pyproject.toml                                           # Root uv workspace configuration
├── package.json                                             # Root pnpm workspace configuration
│
├── apps/
│   ├── control-api/                                         # FastAPI Control Plane (Port 8000)
│   ├── agent-runtime/                                       # FastAPI Execution Engine (Port 8001)
│   └── studio-web/                                          # Next.js 15 Console (Port 3000)
│
├── libs/python/
│   ├── agent-contracts/                                     # Pydantic v2 models sinh từ JSON Schema
│   └── platform-common/                                     # Types dùng chung: Principal, Tenant, RFC 9457 Errors
│
├── contracts/
│   ├── COMPATIBILITY.md                                     # Quy định tương thích ngược
│   ├── jsonschema/                                          # 7 JSON Schemas chuẩn (v1)
│   └── openapi/                                             # OpenAPI specifications
│
├── database/
│   ├── migrations/0001_baseline.sql                         # Khởi tạo toàn bộ 56 bảng Day-1
│   └── tests/0002_smoke.sql                                 # Kịch bản kiểm tra tĩnh toàn vẹn DB
│
├── docs/                                                    # Hệ thống 10 thư mục tài liệu quản trị
├── infra/                                                   # Docker Compose, PostgreSQL, MinIO, OTel
└── tests/                                                   # Bộ kiểm thử: Contract, Security, Recovery, E2E
```

### 16.2. Bảng Quy ước Đặt tên Tập trung

| Đối tượng | Quy ước Đặt tên | Ví dụ Chuẩn |
|---|---|---|
| Python File / Module | `snake_case` | `publish_agent.py` |
| Python / TS Class | `PascalCase` | `AgentPublication` |
| Python Function / Variable | `snake_case` | `reserve_tool_operation()` |
| TypeScript Function / Variable | `camelCase` | `publishAgent()` |
| React Component | `PascalCase.tsx` | `PublishButton.tsx` |
| HTTP Route Segment | `kebab-case` số nhiều | `/tool-operations` |
| Database Table / Column | `snake_case` số nhiều / số ít | `agent_versions` / `workspace_id` |
| Biến Môi trường | `UPPER_SNAKE_CASE` | `CONTROL_API_URL` |
| Định danh Sự kiện | `noun.past_tense.v1` | `agent.published.v1` |
| Hợp đồng Schema File | `name.v1.schema.json` | `agent-spec.v1.schema.json` |
| File ADR | `ADR-XXX-slug.md` | `ADR-008-12-step-tool-dispatcher.md` |
| Test Case Function | Behavior-oriented | `test_publish_rejects_cross_tenant_skill` |

### 16.3. Định dạng Commit Chuẩn (Conventional Commits)
Sử dụng chuẩn Conventional Commits: `feat(runtime): ...`, `fix(tool): ...`, `docs(governance): ...`, `test(contract): ...`.

---

## 17. Cấu hình Công cụ Chuẩn (Tooling Reference)

### 17.1. Cấu hình Python (`pyproject.toml`)
```toml
[project]
name = "enterprise-agent-platform-workspace"
version = "0.0.0"
requires-python = ">=3.12,<3.14"
dependencies = []

[tool.uv.workspace]
members = ["apps/control-api", "apps/agent-runtime", "libs/python/*"]

[tool.ruff]
target-version = "py312"
line-length = 100
src = ["apps/control-api/src", "apps/agent-runtime/src", "libs/python/*/src"]

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "UP", "B", "ASYNC", "C4", "SIM", "RUF"]
ignore = []

[tool.mypy]
python_version = "3.12"
strict = true
warn_unreachable = true
pretty = true
show_error_codes = true

[[tool.mypy.overrides]]
module = ["langgraph.*", "qdrant_client.*"]
ignore_missing_imports = true
```

### 17.2. Cấu hình TypeScript (`tsconfig.json`)
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noFallthroughCasesInSwitch": true,
    "noImplicitOverride": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true
  }
}
```

### 17.3. Các Lệnh Thực thi CI Chuẩn
```bash
# Kiểm tra định dạng & Lint Python
uv run ruff format --check .
uv run ruff check .

# Kiểm tra tĩnh kiểu dữ liệu Python
uv run mypy apps/control-api/src apps/agent-runtime/src libs/python/

# Chạy kiểm thử tự động
uv run pytest tests/contract/

# Kiểm tra Frontend TypeScript
pnpm --filter studio-web lint
pnpm --filter studio-web typecheck
```

---

## 18. Quy trình PR, Checklist Đánh giá Mã Nguồn & Prompt Giao việc

### 18.1. PR Template & Bảng Bằng chứng Evidence
Khi mở Pull Request, bắt buộc điền đầy đủ template:

```markdown
## SPEC / CR Tham chiếu
SPEC-... hoặc CR-...

## Phạm vi Thay đổi (Scope)
- Modules & file paths bị ảnh hưởng:
- Tác động tới Contract / Schema / Migration:
- Tác động tới Ranh giới Tin cậy & Quyền hạn:

## Bằng chứng Kiểm thử Thực tế (Evidence)
| Cổng Kiểm tra (Gate) | Lệnh Thực thi | Kết quả | Bằng chứng / Output |
|---|---|---|---|
| Format & Lint | `uv run ruff check .` | PASS/FAIL/NOT_RUN/BLOCKED | ... |
| Static Typecheck | `uv run mypy ...` | PASS/FAIL/NOT_RUN/BLOCKED | ... |
| Unit / Contract Tests | `uv run pytest ...` | PASS/FAIL/NOT_RUN/BLOCKED | ... |
| Security / RLS Tests | `pytest tests/security/` | PASS/FAIL/NOT_RUN/BLOCKED | ... |

## Kế hoạch Phục hồi & Rủi ro (Rollback Plan)
- Giới hạn đã biết:
- Kế hoạch Rollback an toàn:
```

### 18.2. Code Review Checklist (12 Tiêu chí Đánh giá)
- [ ] **1. Naming & Units:** Tên biến/hàm phản ánh đúng nghiệp vụ; có hậu tố đơn vị thời gian/chi phí rõ ràng.
- [ ] **2. Function Cohesion:** Hàm đơn nhiệm, kích thước tinh gọn, guard clauses xử lý sớm lỗi.
- [ ] **3. Strict Typing:** Python và TypeScript có type annotations đầy đủ; không có `Any` / `any` thiếu căn cứ.
- [ ] **4. Contract Compliance:** DTOs khớp chính xác với JSON Schema & OpenAPI; không tự bịa trường dữ liệu.
- [ ] **5. Boundary Validation:** Validate cú pháp tại ranh giới; phân quyền thực hiện độc lập tại server-side.
- [ ] **6. Error Handling:** Bắt exception cụ thể; giữ nguyên ngữ cảnh lỗi gốc; log không chứa bí mật.
- [ ] **7. Concurrency Safety:** Mỗi task async dùng 1 `AsyncSession` riêng; transaction CSDL ngắn.
- [ ] **8. SQL & Tenancy:** SQL dùng parameter binding 100%; có tenant/workspace filter; phân trang có chặn trên.
- [ ] **9. Frontend Safety:** Next.js Server-First; không để lộ `secret_ref` sang client; chống double-click submit.
- [ ] **10. AI Runtime Gate:** Tool/Skill/MCP bắt buộc đi qua 12-Step ToolDispatcher; đầu ra LLM được validate.
- [ ] **11. Test Coverage:** Có test cho behavior, edge cases và negative security tests; không sleep giả định.
- [ ] **12. Clean Diff:** Pull request tập trung đúng phạm vi; không chứa rác sinh tự động hay refactor lan man.

### 18.3. Prompt Mẫu Giao việc Cho Coding Agent
```text
Hãy áp dụng bộ quy chuẩn `docs/00-governance/CODING_STANDARDS_AND_ARCHITECTURE_RULES.md` vào phạm vi PR hiện tại.
Đọc kỹ 'AGENTS.md', SPEC module tương ứng và contracts trước khi code.
Yêu cầu bắt buộc:
1. Đặt tên rõ nghĩa, strict typing, guard clauses, xử lý lỗi cụ thể, SQL parameterized, bounded work.
2. Không tự ý tạo bảng CSDL, enum, API field mới nếu chưa có contract được duyệt.
3. Chạy formatter, linter, typecheck và tests thật; xuất bảng kết quả PASS/FAIL/NOT_RUN kèm lệnh và bằng chứng.
4. Giữ diff nhỏ gọn, không refactor ngoài phạm vi được giao.
```

---

## 19. Lộ trình Áp dụng (Adoption Roadmap) & Definition of Done

### 19.1. Lộ trình Áp dụng 3 Giai đoạn (Adoption Roadmap)
Để tránh tắc nghẽn (Analysis Paralysis), áp dụng bộ quy chuẩn theo 3 cột mốc:
- **Giai đoạn 1 (Milestone A - Foundation Baseline):**
  - Kích hoạt Ruff format/lint và TypeScript strict mode.
  - Khởi tạo 56 bảng CSDL chuẩn hóa Day-1 và bật Force RLS.
  - Thực thi kiểm thử hợp đồng JSON Schema hai chiều.
- **Giai đoạn 2 (Milestone B - Core Services):**
  - Áp dụng cấu trúc Ports & Adapters và Clean Architecture cho `control-api` và `agent-runtime`.
  - Triển khai 12-Step ToolDispatcher, Idempotency store và outbox receipts.
  - Tích hợp nạp động Skill Engine và kết nối MCP Gateway an toàn.
- **Giai đoạn 3 (Milestone C/D - Production Readiness):**
  - Áp dụng toàn diện các gate kiểm tra Mutation Testing, AST Sandbox, và Chaos Recovery.
  - Hoàn thiện giao diện Studio Web Console và audit log bất biến.

### 19.2. Tiêu chuẩn Hoàn thành (Definition of Done - DoD)
Một tính năng hoặc module chỉ được coi là hoàn thành khi:
1. **Mã nguồn:** Tuân thủ 100% các quy tắc trong tài liệu này; không có linter error hay typecheck failure.
2. **Hợp đồng:** OpenAPI và JSON Schema được cập nhật đồng bộ, có tests xác nhận tính tương thích.
3. **Cơ sở dữ liệu:** Migrations áp dụng thành công; RLS được kiểm chứng bằng negative tests.
4. **Kiểm thử:** Đạt đầy đủ Unit, Integration, và Contract tests với bằng chứng lệnh thực thi thật.
5. **Vận hành:** Có structured logs, metrics và trace context được cấu hình đầy đủ.

---

## 20. Nguồn Tham khảo Kỹ thuật Chính thức

1. **Python Typing Best Practices:** https://typing.python.org/en/latest/reference/best_practices.html
2. **Ruff Linter & Formatter Rules:** https://docs.astral.sh/ruff/linter/
3. **Mypy Strict Mode Documentation:** https://mypy.readthedocs.io/en/stable/command_line.html
4. **SQLAlchemy 2.0 Async Basics:** https://docs.sqlalchemy.org/en/20/orm/session_basics.html
5. **FastAPI Error Handling Patterns:** https://fastapi.tiangolo.com/tutorial/handling-errors/
6. **TypeScript Handbooks & Discriminated Unions:** https://www.typescriptlang.org/docs/handbook/2/narrowing.html
7. **Next.js App Router Architecture:** https://nextjs.org/docs/app/getting-started/server-and-client-components
8. **OWASP Input Validation Cheat Sheet:** https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
9. **RFC 9457 Problem Details for HTTP APIs:** https://datatracker.ietf.org/doc/html/rfc9457

