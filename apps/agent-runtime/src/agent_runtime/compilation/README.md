# `compilation/` — Trình biên dịch đặc tả Agent & Workflow (Spec Compiler)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`compilation/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md), [`ADR-004 Schema-First`](../../../../../docs/02-architecture/adr/ADR-004-schema-first.md), [`agent-spec.v1.schema.json`](../../../../../contracts/jsonschema/agent-spec.v1.schema.json)

---

## 1. Mục đích

Package `compilation/` được chỉ định chịu trách nhiệm xây dựng đường ống biên dịch để chuyển đổi các bản đặc tả mang tính khai báo và có quản lý phiên bản (`AgentSpec` và `WorkflowSpec`) tuân thủ hợp đồng JSON Schema thành các biểu diễn đồ thị thực thi cụ thể.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Xác thực các tệp kê khai JSON `AgentSpec` và `WorkflowSpec` đã được ghim đối chiếu với `contracts/jsonschema/`.
- Biên dịch các định nghĩa Agent mức cao thành các node, cạnh, hàm giảm trạng thái (reducers), và bảng định tuyến thực thi được.
- Xác thực các tham chiếu phiên bản, liên kết công cụ (tool bindings), và phụ thuộc skill trong quá trình biên dịch.
- Tính toán mã băm biên dịch tất định để đảm bảo đồ thị thực thi có khả năng tái tạo (reproducible).

**Phạm vi không thuộc về module:**
- Thực thi đồ thị hoặc chuyển đổi trạng thái (thuộc sở hữu của `engines/` và `application/state_graph.py`).
- Cấp quyền thực thi công cụ (thuộc thẩm quyền của `tools/dispatcher.py`).
- Xuất bản phiên bản Agent bất biến vào danh mục (thuộc thẩm quyền của `apps/control-api`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Trình biên dịch đặc tả (Spec Compiler) | `NOT_IMPLEMENTED` | Tệp `compilation/__init__.py` hiện đang rỗng |
| Trình tạo cây AST đồ thị | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Bộ thẩm định Node & Cạnh | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`__init__.py`](__init__.py) | *(Không có)* | Tệp đánh dấu package; hiện chưa định nghĩa bất kỳ class hoặc hàm nào. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

*Hiện tại chưa có bất kỳ giao diện công khai nào được triển khai trong package này.*

Giao diện kế hoạch được quy định trong [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md):
```python
# Giao diện mục tiêu đề xuất
class AgentCompiler:
    def compile_agent_spec(self, spec: dict[str, Any]) -> CompiledGraphDefinition: ...
```

Các schema hợp đồng liên quan:
- [`contracts/jsonschema/agent-spec.v1.schema.json`](../../../../../contracts/jsonschema/agent-spec.v1.schema.json)
- [`contracts/jsonschema/workflow-spec.v1.schema.json`](../../../../../contracts/jsonschema/workflow-spec.v1.schema.json)

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt.*

Quy trình biên dịch theo thiết kế:
```
AgentSpec (JSON) ──► Schema Validator ──► Dependency Resolver ──► Graph Compiler ──► Đồ thị thực thi đã biên dịch
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến import từ thư viện `agent_contracts` (`libs/python/agent-contracts`) để lấy các model Pydantic.
- Dự kiến xuất các định nghĩa đồ thị đã biên dịch cho `engines/` hoặc `application/state_graph.py`.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Xác thực phiên bản ghim (Pinned Version Validation): Trình biên dịch phải đảm bảo toàn bộ các phiên bản công cụ và skill được tham chiếu đều tồn tại và mang tính bất biến.
- Từ chối đồ thị dị dạng: Ngăn chặn các đồ thị có chu trình lặp vô tận mà không có điều kiện dừng tường minh, các vòng lặp retry không giới hạn, hoặc các khóa trạng thái không được khai báo trước.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Quá trình biên dịch mang tính hàm thuần túy và tất định (đầu vào -> tạo ra artifact đã biên dịch).
- Việc biên dịch lại cùng một bản đặc tả đầu vào bắt buộc phải tạo ra cấu trúc đồ thị và mã băm nội dung giống hệt nhau.

---

## 10. Kiểm thử & Gỡ lỗi

### Các lệnh kiểm thử thực tế đã xác minh
```powershell
# Chạy bài test hợp đồng xác thực roundtrip của JSON schema AgentSpec
python -m pytest tests/contract/test_agent_spec_roundtrip.py -v
```
*Bằng chứng thực tế*: **1 passed in 0.08s** (Kiểm tra trực tiếp JSON schema; bản thân module compiler chưa có unit test).

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: Toàn bộ package đang ở dạng khung rỗng (`__init__.py` có dung lượng 0 bytes). Hiện tại runtime đang khởi tạo đồ thị thủ công hoặc thông qua các script kiểm thử mà chưa có một đường ống biên dịch chuyên dụng.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md)
- Điều kiện cập nhật: Bổ sung nội dung khi bắt đầu triển khai đường ống biên dịch AgentSpec sang đồ thị LangGraph.
