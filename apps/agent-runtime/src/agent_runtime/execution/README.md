# `execution/` — Động cơ phối hợp & Quản lý vòng đời Run (Run Execution Engine)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`execution/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md), [`ADR-006 Durable State`](../../../../../docs/02-architecture/adr/ADR-006-durable-state.md)

---

## 1. Mục đích

Package `execution/` được thiết kế để điều phối vòng đời thực thi từ đầu đến cuối của các lần chạy Agent Run: quản lý các chuyển đổi trạng thái của máy trạng thái lần chạy, lập lịch, phối hợp luồng worker, áp dụng chính sách thử lại (retry), xử lý tín hiệu hủy bỏ (cancellation), và giám sát quá thời gian thực thi (timeout).

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Dẫn dắt các bước chuyển đổi trạng thái: `queued` -> `running` -> `waiting_approval` -> `succeeded` / `failed` / `cancelled` / `timed_out`.
- Thực thi giới hạn thời gian chạy tối đa và ngân sách số bước thực hiện của lần chạy.
- Tiếp nhận và xử lý các yêu cầu hủy bỏ bất đồng bộ để dừng đồ thị thực thi một cách an toàn.
- Phối hợp giữa các worker lấy job trong hàng đợi nền với bộ máy thực thi đồ thị.

**Phạm vi không thuộc về module:**
- Các endpoint API tiếp nhận HTTP hoặc phát luồng SSE (thuộc sở hữu của `api/`).
- Cấp quyền tác dụng phụ của công cụ (thuộc thẩm quyền tối cao của `tools/dispatcher.py`).
- Đưa ra quyết định phê duyệt của con người (thuộc quyền sở hữu của Control API).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Bộ điều phối vòng đời Run | `NOT_IMPLEMENTED` | Tệp `execution/__init__.py` hiện đang rỗng |
| Giám sát Timeout / Hủy bỏ Run | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| *(Tiếp nhận khởi tạo cơ bản)* | `LOCATED_ELSEWHERE` | Hiện được cài đặt trực tiếp tại [`api/runs.py`](../api/runs.py) |

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
class RunCoordinator:
    async def schedule_run(self, run_id: str, priority: int = 0) -> None: ...
    async def cancel_run(self, run_id: str, reason: str) -> None: ...
    async def handle_timeout(self, run_id: str) -> None: ...
```

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt.*

Quy trình chuyển đổi trạng thái theo thiết kế:
```
queued ──► running ──► [Công cụ yêu cầu phê duyệt] ──► waiting_approval
             │                                                  │
             ▼                                                  ▼
   [succeeded / failed]                              [đã phê duyệt] ──► queued
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến phối hợp với `persistence/` để cập nhật trạng thái các bảng `runtime.runs` và `runtime.jobs`.
- Dự kiến được gọi bởi tiến trình worker nền `apps/jobs-worker`.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Khóa trạng thái nguyên tử: Các chuyển đổi trạng thái phải tuân thủ nguyên tắc Compare-And-Swap (CAS) để tránh tranh chấp dữ liệu giữa các luồng worker chạy song song.
- Cô lập theo Tenant: Hàng đợi lập lịch phải tôn trọng giới hạn tải và số lượng tác vụ đồng thời của từng tenant.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Xử lý các lần chạy bị bỏ rơi (Abandoned Runs): Sự cố sập worker phải được phát hiện qua nhịp tim (heartbeat) trong bảng `runtime.jobs`, cho phép tái lập lịch hoặc đánh dấu quá thời gian (timed_out) cho các job chưa hoàn tất.

---

## 10. Kiểm thử & Gỡ lỗi

### Trạng thái kiểm thử
```
Không tìm thấy bài test nào cho package execution/.
```
*(Bài test tạo run tại API liên quan: `apps/agent-runtime/tests/test_health.py` và `tests/e2e/test_agent_publish_and_run.py`).*

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: `execution/` hoàn toàn rỗng. Các truy vấn trạng thái cơ bản hiện đang được viết bằng SQL thô trực tiếp trong `api/runs.py`, và việc thực thi job nền trong `apps/jobs-worker` hiện đang bị ngắt kết nối do thiếu một `RunExecutor` thực thụ.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md)
- Điều kiện cập nhật: Bổ sung nội dung khi triển khai `RunCoordinator` trung tâm và khung thực thi worker.
