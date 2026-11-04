# Bộ máy thực thi Agent (Agent Runtime Package — `agent_runtime`)

> **Package:** `apps/agent-runtime/src/agent_runtime/`  
> **Dịch vụ:** Agent Execution Runtime v2.0  
> **Trạng thái:** `PARTIAL` (Cổng kiểm soát thực thi công cụ - Tool Gateway lõi, MCP Client, các cơ chế kiểm soát an toàn - Guardrails, kiểm tra sức khỏe hệ thống & Ingress đã triển khai; LangGraph Checkpointing, LLM Gateway, lắp ráp ngữ cảnh, bộ nhớ dài hạn, và Worker thực thi Run đang ở dạng khung Skeleton/chưa kết nối)  
> **Tài liệu SPEC kiến trúc liên quan:** [`SPEC-RUNTIME-001.md`](../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md), [`ADR-002 Dual Plane`](../../../../docs/02-architecture/adr/ADR-002-dual-plane-modular-monolith.md), [`ADR-003 Runtime Abstraction`](../../../../docs/02-architecture/adr/ADR-003-runtime-abstraction.md), [`ADR-008 Tool Dispatcher`](../../../../docs/02-architecture/adr/ADR-008-12-step-tool-dispatcher.md), [`CANONICAL_DATABASE_56.md`](../../../../docs/00-governance/CANONICAL_DATABASE_56.md)

---

## 1. Mục đích & Phân tách trách nhiệm kiến trúc

Package `agent_runtime` là động cơ thuộc mặt phẳng thực thi (Execution Plane / Data Plane) của nền tảng Enterprise AI Agent Platform. Trách nhiệm kiến trúc duy nhất của module này là thực thi các phiên bản Agent đã xuất bản (published) và được ghim cố định (pinned), hoạt động trong các ranh giới cô lập dữ liệu theo tenant và workspace tuyệt đối dưới mô hình quản trị Zero-Trust.

### Phân định kiến trúc hệ thống
* **Control API (`apps/control-api`)**: Nắm giữ mặt phẳng điều khiển (Control Plane) — xác thực người dùng, phân quyền RBAC thành viên, soạn thảo Agent, xuất bản phiên bản (revision publishing), quản lý phiên bản danh mục (catalog versioning), quản trị cấp quyền công cụ (tool grants) và quản lý phê duyệt quản trị.
* **Agent Runtime (`apps/agent-runtime`)**: Nắm giữ mặt phẳng dữ liệu/thực thi (Data/Execution Plane) — xác minh token JWT phi trạng thái (HMAC-SHA256), tiếp nhận yêu cầu thực thi Run, xếp hàng các tác vụ xử lý (execution jobs), chạy quy trình đồ thị (graph workflows), thực thi giao thức 12 bước của Cổng kiểm soát thực thi công cụ (Tool Gateway), gọi các công cụ MCP từ xa với cơ chế phòng thủ SSRF, và phát luồng sự kiện thời gian thực (Server-Sent Events - SSE). Module này chỉ thực thi các phiên bản bất biến đã được xuất bản.
* **Studio Web (`apps/studio-web`)**: Mặt phẳng trình diễn (Presentation Plane) cung cấp giao diện người dùng đồ họa để thiết kế Agent, đưa ra quyết định phê duyệt của con người (HITL), giám sát thực thi và tương tác trò chuyện.

---

## 2. Điểm vào thực thi & Giao diện dòng lệnh (CLI)

Bộ máy thực thi Agent (Agent Runtime) cung cấp dịch vụ ASGI tương thích HTTP/1.1 và HTTP/2 được xây dựng trên nền tảng FastAPI.

### Các điểm vào đã kiểm chứng
* **Ứng dụng FastAPI**: [`agent_runtime.main:app`](main.py)
* **Bộ nạp cấu hình**: [`agent_runtime.settings.get_settings()`](settings.py)
* **Dependency xác thực quyền**: [`agent_runtime.auth.get_runtime_principal`](auth.py)
* **Các Router tiếp nhận (Ingress Routers)**:
  * Quản lý lần thực thi Agent (Agent Run): [`api/runs.py`](api/runs.py) (`POST /v1/runs`, `GET /v1/runs/{run_id}`, `POST /internal/v1/runs/{run_id}/resume`)
  * Phát luồng sự kiện: [`api/events.py`](api/events.py) (`GET /v1/runs/{run_id}/events`)
  * Thăm dò sức khỏe chuyên sâu (Deep Health Probes): [`api/health.py`](api/health.py) (`GET /health`, `GET /health/live`, `GET /health/ready`)

### Lệnh khởi chạy cục bộ đã xác thực
```bash
# Chạy từ thư mục gốc repository sử dụng UV
uv run uvicorn agent_runtime.main:app --app-dir apps/agent-runtime/src --host 0.0.0.0 --port 8001
```

---

## 3. Vòng đời thực thi (Execution Lifecycle)

Luồng thực thi từ đầu đến cuối xuyên suốt mã nguồn hiện tại được cấu trúc như sau:

```mermaid
flowchart TD
    Client["Client / Studio Web"] -->|1. POST /v1/runs + Bearer JWT + Idempotency-Key| Ingress["api/runs.py: start_run"]
    Ingress -->|2. verify_runtime_token| Auth["auth.py: RuntimePrincipal"]
    Auth -->|3. Validate tenant_id & workspace_id match claims| Ingress
    Ingress -->|4. Compute request_digest SHA256| Ingress
    Ingress -->|5. INSERT runtime.runs + runtime.jobs + runtime.run_events| DB[("PostgreSQL DB-56")]
    
    DB -.->|Job Queued| Worker["apps/jobs-worker (Disconnected)"]
    Worker -.->|GAP: _run_executor is None| StateGraph["application/state_graph.py: StateGraphEngine"]
    
    StateGraph -->|Node Execution| Node["Agent Logic Node"]
    Node -->|Tool Call Request| Dispatcher["tools/dispatcher.py: ToolDispatcher"]
    Dispatcher -->|12-Step Validation & Approval| DB
    Dispatcher -->|McpToolAdapter| MCP["mcp/client.py: McpClientAdapter"]
    MCP -->|Streamable HTTP POST| RemoteMCP["External MCP Server"]
    RemoteMCP -->|Result| MCP
    MCP -->|Result| Dispatcher
    Dispatcher -->|Write receipt & audit_events| DB
    
    Client -->|6. GET /v1/runs/{run_id}/events| SSE["api/events.py: stream_events"]
    SSE -->|Read runtime.run_events| DB
    SSE -->|text/event-stream| Client
```

### Trạng thái các chặng xử lý & Các điểm ngắt kết nối
1. **Từ yêu cầu đến hàng đợi (`IMPLEMENTED`)**: `api/runs.py` xác thực các claims trong JWT dựa trên `RuntimeSettings`, tính toán `request_digest`, áp dụng tính bất biến khi thực hiện lặp (idempotency) trên bộ khóa `(tenant_id, workspace_id, external_request_key)`, và chèn nguyên tử (atomic) các bản ghi vào `runtime.runs`, `runtime.jobs`, và `runtime.run_events`.
2. **Phát luồng sự kiện (`IMPLEMENTED`)**: `api/events.py` xác minh phạm vi workspace của người gọi và phát luồng các sự kiện có thứ tự qua SSE (`text/event-stream`) trực tiếp từ bảng `runtime.run_events`.
3. **Từ hàng đợi đến Worker xử lý đồ thị (`DISCONNECTED / CODE_GAP`)**: `apps/jobs-worker` thăm dò bảng `runtime.jobs`, nhưng `JobWorker.from_environment()` khởi tạo với `_run_executor = None`. Việc cố gắng nhận và thực thi một run job sẽ ném ngoại lệ `JobExecutionError("No real run executor is configured")`.
4. **Bộ máy thực thi đồ thị (`PARTIAL / GAP`)**: `application/state_graph.py` triển khai một state machine tự xây dựng (`StateGraphEngine`) với các lệnh put/get cơ bản tới checkpointer, nhưng ĐÂY KHÔNG PHẢI là LangGraph Pregel và đang thiếu một checkpointer PostgreSQL hoàn chỉnh kết nối với bảng `runtime.checkpoint_refs`.
5. **Thực thi công cụ (`PARTIAL / ORPHANED`)**: `tools/dispatcher.py` triển khai đầy đủ 12 bước xác thực, kiểm tra phê duyệt (approval), đặt trước tính bất biến (idempotency reservation), xác thực schema, và ghi audit log cuối cùng, tuy nhiên hiện tại nó chưa được kết nối vào các node của `StateGraphEngine`.

---

## 4. Danh mục Module & Trạng thái triển khai

| Module | Đường dẫn tương đối | Quyền sở hữu / Trách nhiệm | Trạng thái |
|---|---|---|---|
| **api** | [`api/`](api/README.md) | Các route HTTP tiếp nhận (`/v1/runs`, `/internal/.../resume`, `/v1/runs/{id}/events`, `/health`) | `PARTIAL` |
| **application** | [`application/`](application/README.md) | Bộ máy đồ thị trạng thái điều phối, quản lý phê duyệt trong bộ nhớ, outbox SSE | `PARTIAL` |
| **compilation** | [`compilation/`](compilation/README.md) | Xác thực schema và biên dịch đồ thị từ AgentSpec/WorkflowSpec | `SKELETON` |
| **engines** | [`engines/`](engines/README.md) | Adapter bộ máy thực thi LangGraph và quản lý vòng đời đồ thị | `SKELETON` |
| **agents** | [`agents/`](agents/README.md) | Các mẫu hành vi Agent (ReAct, Bounded Planner, Digital Worker) | `SKELETON` |
| **execution** | [`execution/`](execution/README.md) | Máy trạng thái vòng đời Run, lập lịch, cơ chế thử lại (retry), và hủy bỏ (cancellation) | `SKELETON` |
| **tools** | [`tools/`](tools/README.md) | Cổng kiểm soát thực thi công cụ (Tool Gateway) 12 bước chuẩn hóa trên PostgreSQL, tính bất biến, ghi nhật ký kiểm toán | `PARTIAL` |
| **skills** | [`skills/`](skills/README.md) | Nạp thông tin theo nhu cầu (Progressive Disclosure) 3 lớp (Khám phá, nạp SKILL.md, đọc tài nguyên) | `PARTIAL` |
| **mcp** | [`mcp/`](mcp/README.md) | Adapter client giao thức MCP qua Streamable HTTP với cơ chế bảo vệ SSRF egress chuyên sâu | `PARTIAL` |
| **context** | [`context/`](context/README.md) | Tam giác độ tin cậy (CircuitBreaker, FallbackLadder, SemanticVectorCache) | `PARTIAL` |
| **memory** | [`memory/`](memory/README.md) | Kho lưu trữ bộ nhớ dài hạn, truy hồi xuyên suốt các lần chạy, duy trì hồ sơ người dùng | `SKELETON` |
| **knowledge** | [`knowledge/`](knowledge/README.md) | Embeddings E5 đa ngôn ngữ, truy xuất lai trên Qdrant, kết hợp BM25+RRF, xác minh trích dẫn | `PARTIAL` |
| **llm** | [`llm/`](llm/README.md) | Adapter cổng kết nối nhà cung cấp Model Gateway, thang dự phòng, hạch toán token/chi phí | `SKELETON` |
| **safety** | [`safety/`](safety/README.md) | Phòng thủ 4 lớp: ZeroWidthShield, làm sạch PII tiếng Việt, PolicyEngine PEP, ASTSandbox | `PARTIAL` |
| **persistence** | [`persistence/`](persistence/README.md) | Quản lý kết nối PostgreSQL bất đồng bộ, pool kết nối, ngữ cảnh phiên RLS | `PARTIAL` |
| **observability** | [`observability/`](observability/README.md) | Lan truyền ngữ cảnh traceparent W3C và 11 span thực thi chuẩn hóa | `PARTIAL` |

---

## 5. Hướng phụ thuộc & Đồ thị Import

Bộ máy thực thi Agent tuân thủ các nguyên lý kiến trúc Hexagonal (Ports-and-Adapters):

```
[ Ingress: api/ ] 
       │
       ▼
[ Application Orchestration: application/ ] 
       │
       ├──► [ Safety Guardrails: safety/ ]
       ├──► [ Context & Reliability: context/ ]
       ├──► [ Knowledge Substrate: knowledge/ ]
       ├──► [ Observability: observability/ ]
       │
       ▼
[ Execution Boundaries: tools/ ] 
       │
       ├──► [ Skills: skills/ ]
       └──► [ MCP Client: mcp/ ]
       │
       ▼
[ Persistence & Database: persistence/ ] ──► PostgreSQL (DB-56)
```

### Các sai lệch & Khoảng trống kiến trúc
* Module `context/` hiện chứa các thành phần phục hồi hạ tầng (`CircuitBreaker`, `FallbackLadder`, `SemanticVectorCache`) thay vì chứa logic lắp ráp ngữ cảnh prompt/token.
* `application/approval_manager.py` quản lý các phê duyệt trong một dictionary cục bộ trong bộ nhớ thay vì truy vấn bảng `runtime.approval_requests` (nơi mà `tools/dispatcher.py` truy vấn đúng chuẩn).
* `tools/dispatcher.py` thực thi trực tiếp các câu lệnh SQL thông qua SQLAlchemy đối với các bảng DB-56 thay vì phụ thuộc vào một giao diện repository phân tách.

---

## 6. Các bất biến bảo mật xuyên suốt (Cross-Cutting Security)

1. **Phạm vi Server-Side theo mô hình Zero-Trust**: `tenant_id` và `workspace_id` được trích xuất hoàn toàn từ các claims trong token JWT đã được xác thực mã hóa (`auth.py: RuntimePrincipal`), tuyệt đối không nhận từ tham số đầu vào của client, header tùy ý hoặc payload do LLM sinh ra.
2. **Bảo mật cấp hàng (Row-Level Security - RLS)**: Mỗi giao dịch cơ sở dữ liệu được mở qua `persistence.database.scoped_session` đều thực thi:
   ```sql
   SELECT set_config('app.tenant_id', :tenant_id, true);
   SELECT set_config('app.workspace_id', :workspace_id, true);
   ```
   đảm bảo cơ chế đóng an toàn khi lỗi (fail-closed) của PostgreSQL RLS ở cấp độ phiên làm việc (session level).
3. **Phòng thủ SSRF luồng ra (Egress)**: Các lệnh gọi công cụ từ xa thông qua `mcp.client.McpClientAdapter` giải đối chiếu tên miền và chặn toàn bộ các dải IP loopback, dải riêng tư RFC1918, link-local, multicast, kỹ thuật vượt rào IP số nguyên (integer IP bypass), và địa chỉ metadata của dịch vụ đám mây (169.254.169.254).
4. **Tham chiếu ghim bất biến (Immutable Pinned References)**: Các lần thực thi Run tham chiếu đến các định danh bất biến `agent_version_id`, `tool_version_id`, và `skill_version_id`. Quá trình thực thi công cụ kiểm tra chặt chẽ schema khám phá được từ MCP phải khớp với `approved_schema_sha256`.
5. **Tính bất biến bền vững & Khóa băm hành động (Action Hashing)**: Các thao tác ghi công cụ có rủi ro cao đòi hỏi phải có một bản ghi phê duyệt chưa hết hạn trong `runtime.approval_requests` khớp chính xác với mã băm SHA256 `action_hash` của payload gọi chuẩn hóa. Các yêu cầu trùng lặp được khử trùng lặp qua bộ khóa `(tenant_id, workspace_id, operation_key)`.

---

## 7. Ranh giới Trạng thái & Dữ liệu

| Vùng dữ liệu | Bảng chịu trách nhiệm | Quy tắc sở hữu |
|---|---|---|
| **Runs & Sự kiện** | `runtime.runs`, `runtime.run_events`, `runtime.jobs` | Thuộc sở hữu của `api/runs.py` và `jobs-worker`. Số thứ tự sự kiện (sequence) tăng đơn điệu theo từng lần chạy. |
| **Thao tác công cụ** | `runtime.tool_operations` | Thuộc quyền kiểm soát tối cao của `tools/dispatcher.py: ToolDispatcher`. |
| **Phê duyệt (Approvals)** | `runtime.approval_requests` | Được đọc có thẩm quyền bởi `tools/dispatcher.py`; được ghi bởi Control API. |
| **Điểm lưu trạng thái (Checkpoints)** | `runtime.checkpoint_refs` | Dành cho các tham chiếu checkpointer trạng thái LangGraph (hiện chưa kết nối đầy đủ). |
| **Vector tri thức** | Qdrant Collection `knowledge_vectors` | Được quản lý có thẩm quyền bởi `knowledge/qdrant_index.py` theo schema `contracts/jsonschema/knowledge-vector.v1.schema.json`. Chỉ chứa vector ID, phiên bản tài liệu, chunk ID, và payload hash (không lưu văn bản thô). |

---

## 8. Phát triển, Kiểm thử & Xác minh

### Các lệnh kiểm thử an toàn
Chạy toàn bộ các bài unit test và contract test trong workspace (chế độ offline, không phá hủy dữ liệu):
```powershell
python -m pytest -m "not integration" -v
```
*Kết quả kiểm thử thực tế*: **52 passed, 4 deselected in 9.00s**.

Chạy riêng kiểm tra endpoint health:
```powershell
python -m pytest apps/agent-runtime/tests/test_health.py -v
```
*Kết quả*: **1 passed**.

Chạy kiểm tra hợp đồng 12 bước của Tool Dispatcher:
```powershell
python -m pytest tests/contract/test_tool_dispatcher_12step.py -v
```
*Kết quả*: **2 passed**.

---

## 9. Các khoảng trống kiến trúc đã ghi nhận

1. **Job Worker bị ngắt kết nối**: Module `jobs-worker` thiếu một triển khai `RunExecutor` thực tế, khiến các tác vụ trong hàng đợi nền không thể được thực thi.
2. **Thiếu tích hợp LangGraph chuẩn**: Mặc dù các đặc tả yêu cầu quy trình làm việc LangGraph Pregel (`ADR-008`), runtime hiện dùng một bộ mô phỏng tự viết `StateGraphEngine` (112 dòng) và thiếu cơ chế checkpointer bền vững trên PostgreSQL.
3. **Các package dạng khung (Skeleton) còn trống**: Các module `agents`, `compilation`, `engines`, `execution`, `llm`, và `memory` hiện chỉ chứa file `__init__.py` rỗng.
4. **Adapter công cụ chưa đầy đủ**: `ToolDispatcher` hiện mới chỉ hỗ trợ công cụ MCP qua `McpToolAdapter`. Các adapter cho công cụ nội hợp (`builtin`) và công cụ HTTP (`http`) chưa được triển khai.
5. **Lệch cột bảng Audit Ledger**: `tools/dispatcher.py` chèn dữ liệu vào bảng `governance.audit_events` với trường `details_redacted`, nhưng schema bảng hiện thiếu các cột chuyên dụng `prev_hash` và `record_hash` cần thiết cho cơ chế chuỗi băm chống giả mạo (tamper-evident).

---

## 10. Quy tắc cập nhật tài liệu

Khi chỉnh sửa các giao diện công khai, luồng chuyển đổi trạng thái, ranh giới bảo mật, hoặc các phụ thuộc trong package `agent_runtime`:
1. Phải cập nhật tệp tài liệu này và tệp `<module>/README.md` tương ứng trong cùng một Pull Request.
2. Kiểm tra xác nhận toàn bộ các liên kết Markdown nội bộ đều tồn tại và hợp lệ.
3. Đảm bảo các lệnh thực thi kiểm thử và kết quả báo cáo phản ánh đúng kết quả chạy thực tế tại môi trường local/CI.
