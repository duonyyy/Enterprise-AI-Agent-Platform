# `agents/` — Các mẫu hình vi & Nguyên mẫu Agent (Agent Behavioral Archetypes)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`agents/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-AGENT-001.md`](../../../../../docs/04-module-specs/agent/SPEC-AGENT-001.md), [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md)

---

## 1. Mục đích

Package `agents/` được dự định để chứa các mẫu hành vi Agent tái sử dụng được và các vòng lặp suy luận (chẳng hạn như vòng lặp ReAct, Bounded Planner, Task Agent, và các nguyên mẫu công nhân số Digital Worker), định nghĩa cách thức Agent lặp tuần tự giữa suy luận LLM, nạp skill và thực thi công cụ.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Chuẩn hóa các vòng lặp điều phối Agent (ví dụ: chu trình Suy luận - Hành động - Quan sát / Reason-Act-Observe).
- Quản trị ngân sách cuộc gọi mô hình (model calls) và ngân sách gọi công cụ (tool calls) nhằm ngăn chặn việc lặp vô hạn.
- Định nghĩa các điều kiện kết thúc vòng lặp (ví dụ: đã rút trích câu trả lời cuối cùng, cần hỏi thêm người dùng, hoặc cần phê duyệt HITL).
- Cấu trúc hóa việc tiêm các mẫu prompt (prompt templates) cho từng nguyên mẫu Agent.

**Phạm vi không thuộc về module:**
- Lược đồ lưu trữ cơ sở dữ liệu hoặc kho lưu điểm trạng thái checkpoint (thuộc quyền sở hữu của `persistence/`).
- Cấp quyền thực thi công cụ (thuộc quyền kiểm soát tối cao của `tools/dispatcher.py`).
- Kết nối trực tiếp tới các nhà cung cấp mô hình AI (thuộc quyền sở hữu của `llm/`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Vòng lặp điều phối ReAct | `NOT_IMPLEMENTED` | Tệp `agents/__init__.py` hiện đang rỗng |
| Agent lập kế hoạch có giới hạn (Bounded Planner) | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Mẫu công nhân số (Digital Worker) | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`__init__.py`](__init__.py) | *(Không có)* | Tệp đánh dấu package; hiện chưa định nghĩa bất kỳ class hoặc hàm nào. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

*Hiện tại chưa có bất kỳ giao diện công khai nào được triển khai trong package này.*

Giao diện kế hoạch được quy định trong [`SPEC-AGENT-001.md`](../../../../../docs/04-module-specs/agent/SPEC-AGENT-001.md):
```python
# Giao diện mục tiêu đề xuất
class BaseAgentPattern(Protocol):
    async def step(self, state: dict[str, Any], budget: ExecutionBudget) -> dict[str, Any]: ...
```

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt.*

Quy trình vòng lặp ReAct theo thiết kế:
```
Quan sát trạng thái ──► Model Gateway (Suy luận) ──► Lập kế hoạch gọi Tool ──► Tool Gateway ──► Nhận kết quả ──► Lặp lại / Kết thúc
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến import các tiện ích suy luận từ `llm/` và phát các lệnh gọi tác dụng phụ thông qua `tools/dispatcher.py`.
- Dự kiến tương tác với `context/` để lắp ráp prompt và quản lý hạn mức token.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Ngân sách thực thi (Execution Budgets): Toàn bộ các mẫu Agent bắt buộc phải áp dụng giới hạn trên nghiêm ngặt đối với số lần gọi mô hình liên tiếp (ví dụ: tối đa 10 bước) và tổng thời gian chạy thực tế để tránh bùng nổ chi phí.
- Thực thi không đặc quyền: Agent không có quyền truy cập hệ thống trực tiếp; mọi hành động bắt buộc phải định tuyến qua Cổng kiểm soát thực thi công cụ (Tool Gateway).

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Các mẫu Agent phải có khả năng bị ngắt quãng tại bất kỳ bước nào để phục vụ cơ chế phê duyệt của con người (HITL) mà không làm mất trạng thái suy luận trung gian.

---

## 10. Kiểm thử & Gỡ lỗi

### Trạng thái kiểm thử
```
Không tìm thấy bài test nào cho package agents/.
```

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: `agents/` chỉ chứa một tệp `__init__.py` rỗng. Hiện tại, logic các node Agent đang được viết tùy biến trong các script kiểm thử hoặc trong các mock runner trong bộ nhớ.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-AGENT-001.md`](../../../../../docs/04-module-specs/agent/SPEC-AGENT-001.md)
- Điều kiện cập nhật: Bổ sung nội dung khi bắt đầu triển khai các mẫu node Agent ReAct hoặc Supervisor chính quy.
