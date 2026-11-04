# `engines/` — Adapter bộ máy thực thi quy trình (Workflow Execution Engine)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`engines/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md), [`ADR-003 Runtime Abstraction`](../../../../../docs/02-architecture/adr/ADR-003-runtime-abstraction.md), [`ADR-006 Durable State`](../../../../../docs/02-architecture/adr/ADR-006-durable-state.md)

---

## 1. Mục đích

Package `engines/` được thiết kế để cung cấp các adapter trừu tượng hóa cho các bộ máy thực thi quy trình (workflow engines), cụ thể là bộ máy LangGraph Pregel phục vụ sản xuất. Module này có nhiệm vụ đóng gói các hành vi thực thi đồ thị, phát luồng trạng thái, lưu điểm trạng thái bền vững (checkpoint persistence), và điều phối việc tạm dừng/tiếp tục luồng chạy (interrupt/resume) phía sau một giao thức chuẩn hóa chung.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Triển khai adapter tích hợp bộ máy LangGraph Pregel.
- Chuẩn hóa giao thức bộ máy thực thi: `compile()`, `invoke()`, `stream()`, `resume()`.
- Gắn kết bộ lưu điểm trạng thái (checkpointer) với cơ sở dữ liệu PostgreSQL (`runtime.checkpoint_refs`).
- Ánh xạ các ngoại lệ runtime đặc thù của engine về các hợp đồng mã lỗi chuẩn của nền tảng.

**Phạm vi không thuộc về module:**
- Các endpoint API tiếp nhận HTTP hoặc xác thực token (thuộc quyền sở hữu của `api/`).
- Cấp quyền hoặc thực thi các tác dụng phụ của công cụ (thuộc quyền kiểm soát tối cao của `tools/dispatcher.py`).
- Các mẫu hành vi Agent cấp cao như ReAct (thuộc quyền sở hữu của `agents/`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Adapter LangGraph Pregel | `NOT_IMPLEMENTED` | Tệp `engines/__init__.py` hiện đang rỗng |
| Giao thức trừu tượng hóa Engine | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| *(Bộ máy thực thi tạm thời)* | `LOCATED_ELSEWHERE` | Class `StateGraphEngine` tự viết hiện nằm tại [`application/state_graph.py`](../application/state_graph.py) |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`__init__.py`](__init__.py) | *(Không có)* | Tệp đánh dấu package; hiện chưa định nghĩa bất kỳ class hoặc hàm nào. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

*Hiện tại chưa có bất kỳ giao diện công khai nào được triển khai trong package này.*

Giao diện giao thức Engine mục tiêu được quy định trong [`ADR-003`](../../../../../docs/02-architecture/adr/ADR-003-runtime-abstraction.md):
```python
# Giao diện mục tiêu đề xuất
class WorkflowEngine(Protocol):
    async def invoke(self, thread_id: str, input_state: dict[str, Any]) -> dict[str, Any]: ...
    async def resume(self, thread_id: str, checkpoint_id: str, resume_payload: dict[str, Any]) -> dict[str, Any]: ...
    async def stream_events(self, thread_id: str) -> AsyncIterator[dict[str, Any]]: ...
```

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt trong package này.*

Vòng đời thực thi theo thiết kế:
```
Job Worker ──► WorkflowEngine.invoke() ──► Đồ thị LangGraph Pregel ──► Checkpointer Put ──► Phát luồng sự kiện
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến import các thư viện bên ngoài `langgraph` và `langchain_core`.
- Dự kiến kết nối với các backend checkpointer trên PostgreSQL do `persistence/` quản trị.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Phân vùng theo luồng (Thread Scoping): Các khóa trạng thái của checkpointer bắt buộc phải được phân vùng theo `tenant_id`, `workspace_id`, và `thread_id` để ngăn ngừa rò rỉ dữ liệu giữa các tenant.
- Giới hạn thời gian thực thi: Các lệnh gọi thực thi đồ thị phải được kiểm soát chặt chẽ bởi ngân sách thực thi (execution budget) và thời gian timeout tối đa.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Các engine phải hỗ trợ việc khôi phục và tiếp tục từ điểm lưu trạng thái (checkpoint) bền vững sau sự cố sập tiến trình bằng cách sử dụng các thread ID ổn định.
- Các tác dụng phụ ra bên ngoài bắt buộc phải được cô lập tuyệt đối bên trong `tools/dispatcher.py` để ngăn ngừa việc ghi dữ liệu trùng lặp khi đồ thị được chạy phát lại (replay).

---

## 10. Kiểm thử & Gỡ lỗi

### Trạng thái kiểm thử
```
Không tìm thấy bài test nào cho package engines/.
```
*(Bài test mô phỏng khôi phục liên quan: `tests/recovery/test_resume_after_restart.py` hiện kiểm tra class `application.state_graph.StateGraphEngine`).*

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: Package `engines/` hoàn toàn rỗng. Logic điều phối đồ thị hiện đang được triển khai bên trong `application/state_graph.py` dưới dạng một lớp mô phỏng tự viết thay vì sử dụng lớp adapter chính quy của `engines/` kết hợp cùng LangGraph.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Quyết định kiến trúc ADR: [`ADR-003 Runtime Abstraction`](../../../../../docs/02-architecture/adr/ADR-003-runtime-abstraction.md)
- Quyết định kiến trúc ADR: [`ADR-006 Durable State`](../../../../../docs/02-architecture/adr/ADR-006-durable-state.md)
- Điều kiện cập nhật: Bổ sung nội dung khi bắt đầu chuyển đổi `StateGraphEngine` sang adapter LangGraph hoàn chỉnh đặt tại `engines/`.
