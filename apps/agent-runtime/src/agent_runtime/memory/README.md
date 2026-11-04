# `memory/` — Kho lưu trữ hồ sơ & Bộ nhớ dài hạn xuyên suốt (Long-Term Memory)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`memory/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-MEMORY-001.md`](../../../../../docs/04-module-specs/memory/SPEC-MEMORY-001.md), [`CANONICAL_DATABASE_56.md`](../../../../../docs/00-governance/CANONICAL_DATABASE_56.md)

---

## 1. Mục đích

Package `memory/` được thiết kế để quản lý bộ nhớ dài hạn bền vững xuyên suốt nhiều lần chạy Run và nhiều phiên trò chuyện khác nhau. Module này xử lý việc rút trích bộ nhớ ngữ nghĩa (semantic memory) và bộ nhớ phân đoạn (episodic memory), quản lý chính sách truy hồi xuyên suốt, lưu trữ hồ sơ sở thích người dùng, áp dụng thời hạn lưu trữ (TTL), và tuân thủ các quy định pháp luật về quyền được lãng quên/xóa dữ liệu.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Lưu trữ và lập chỉ mục các ký ức theo phân đoạn xuyên luồng (cross-thread) cùng các sự thật (facts) trong hồ sơ người dùng.
- Quản lý chính sách truy hồi bộ nhớ và tìm kiếm tương đồng ngữ nghĩa đối chiếu các cuộc trò chuyện trong quá khứ.
- Thực thi sự đồng ý của người dùng, cơ chế hết hạn TTL, và tẩy xóa bộ nhớ theo luật định (Nghị định 13/2023/NĐ-CP).
- Phân vùng dữ liệu bộ nhớ theo tenant ID, workspace ID, và subject/user ID.

**Phạm vi không thuộc về module:**
- Điểm lưu trạng thái thực thi trong phạm vi luồng (thread-scoped checkpoints) phục vụ khôi phục tiến trình (thuộc quyền sở hữu của `persistence/` và `application/state_graph.py`).
- Tập hợp tài liệu văn bản RAG và Knowledge Bases của tổ chức (thuộc thẩm quyền tuyệt đối của `knowledge/`).
- Tương tác trực tiếp với các nhà cung cấp mô hình AI (thuộc sở hữu của `llm/`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Kho lưu trữ bộ nhớ dài hạn | `NOT_IMPLEMENTED` | Tệp `memory/__init__.py` hiện đang rỗng |
| Chính sách truy hồi xuyên suốt | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Trích xuất sự thật hồ sơ người dùng | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Quản lý lưu trữ & Xóa theo TTL | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`__init__.py`](__init__.py) | *(Không có)* | Tệp đánh dấu package; hiện chưa định nghĩa bất kỳ class hoặc hàm nào. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

*Hiện tại chưa có bất kỳ giao diện công khai nào được triển khai trong package này.*

Giao diện kế hoạch được quy định trong [`SPEC-MEMORY-001.md`](../../../../../docs/04-module-specs/memory/SPEC-MEMORY-001.md):
```python
# Giao diện mục tiêu đề xuất
class LongTermMemoryStore(Protocol):
    async def recall(self, tenant_id: str, workspace_id: str, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...
    async def persist_fact(self, tenant_id: str, workspace_id: str, user_id: str, fact: str, ttl_days: int | None = None) -> str: ...
    async def purge_user_memory(self, tenant_id: str, workspace_id: str, user_id: str) -> int: ...
```

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt.*

Vòng đời bộ nhớ theo thiết kế:
```
Kết thúc lượt hội thoại ──► Trích xuất Fact ──► Kiểm tra chính sách / Đồng ý ──► LongTermMemoryStore ──► Truy hồi trong tương lai
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến sử dụng `knowledge.embeddings.E5EmbeddingModel` để tạo vector embeddings cho các bản ghi ký ức.
- Dự kiến lưu trữ các bản ghi ký ức vào các bảng CSDL chuyên dụng hoặc các namespace vector riêng biệt.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Cô lập theo Tenant & Người dùng: Bộ nhớ bắt buộc phải được gắn phạm vi chặt chẽ theo bộ khóa `(tenant_id, workspace_id, user_id)`. Rò rỉ bộ nhớ giữa các người dùng hoặc giữa các workspace là vi phạm bảo mật mức nghiêm trọng nhất.
- Quyền được lãng quên (Right to be Forgotten): Phải hỗ trợ xóa sạch hoàn toàn toàn bộ các dữ liệu và ký ức liên quan tới một định danh người dùng cụ thể.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Việc ghi nhận bộ nhớ phải được thực hiện bất đồng bộ sau khi lần chạy Agent hoàn tất để không làm tăng độ trễ phản hồi của hệ thống.
- Khi kho bộ nhớ gặp sự cố hoặc hư hỏng, hệ thống phải mở an toàn (fail-open) đối với việc thực thi Agent (tức là Agent vẫn tiếp tục hội thoại mà không có quá khứ thay vì dừng toàn bộ tiến trình).

---

## 10. Kiểm thử & Gỡ lỗi

### Trạng thái kiểm thử
```
Không tìm thấy bài test nào cho package memory/.
```

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: Package `memory/` chỉ chứa một tệp `__init__.py` rỗng. Hiện tại chưa có năng lực bộ nhớ dài hạn hay hồ sơ người dùng nào hoạt động trong runtime.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-MEMORY-001.md`](../../../../../docs/04-module-specs/memory/SPEC-MEMORY-001.md)
- Điều kiện cập nhật: Bổ sung nội dung khi triển khai kho lưu trữ và cơ chế truy hồi bộ nhớ dài hạn phân đoạn.
