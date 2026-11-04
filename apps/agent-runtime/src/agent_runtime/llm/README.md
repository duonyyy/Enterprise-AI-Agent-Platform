# `llm/` — Adapter Cổng kết nối mô hình & Nhà cung cấp (Model Gateway)

> **Trạng thái:** `SKELETON / NOT_IMPLEMENTED`  
> **Mã nguồn đã rà soát:** [`llm/__init__.py`](__init__.py) (0 bytes)  
> **Tài liệu SPEC liên quan:** [`SPEC-LLM-001.md`](../../../../../docs/04-module-specs/model-gateway/SPEC-LLM-001.md)

---

## 1. Mục đích

Package `llm/` được thiết kế để đóng vai trò là adapter Cổng kết nối mô hình (Model Gateway) thống nhất cho Bộ máy thực thi Agent (Agent Runtime). Module này trừu tượng hóa các nhà cung cấp mô hình ngôn ngữ lớn (LLM) bên thứ ba (chẳng hạn như OpenAI, Anthropic, Google Gemini, và các cụm vLLM tự host cục bộ), cung cấp cơ chế gửi yêu cầu có khả năng chịu lỗi, trích xuất cấu trúc dữ liệu JSON đầu ra, giới hạn tốc độ (rate limiting), và các hook hạch toán chi phí/token tiêu thụ.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm theo thiết kế (Kế hoạch):**
- Trừu tượng hóa giao diện nhà cung cấp thống nhất (`generate()`, `stream()`).
- Chuyển đổi các thiết lập mô hình chuẩn hóa (temperature, top_p, max_tokens, presence_penalty) thành các payload đặc thù của từng nhà cung cấp.
- Bắt buộc trích xuất đầu ra có cấu trúc thông qua JSON Schema hoặc cơ chế gọi công cụ (tool calling).
- Phát các số liệu sử dụng chi tiết (prompt tokens, completion tokens, chi phí ước tính GPU/API).
- Quản lý thời gian chờ (timeout) và số lần thử lại (retries) khi kết nối tới nhà cung cấp mô hình.

**Phạm vi không thuộc về module:**
- Hardcode các khóa bí mật API của nhà cung cấp (bắt buộc phải giải quyết qua két bí mật server-side vault hoặc biến môi trường bảo mật).
- Cấp quyền gọi công cụ (thuộc quyền kiểm soát tối cao của `tools/dispatcher.py`).
- Máy trạng thái ngắt mạch Circuit Breaker (nằm tại `context/circuit_breaker.py`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Adapter Client Model Gateway | `NOT_IMPLEMENTED` | Tệp `llm/__init__.py` hiện đang rỗng |
| Giao thức trừu tượng hóa nhà cung cấp | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Bộ phân tích đầu ra có cấu trúc | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |
| Hook hạch toán chi phí & Token | `NOT_IMPLEMENTED` | Chưa tìm thấy mã nguồn |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`__init__.py`](__init__.py) | *(Không có)* | Tệp đánh dấu package; hiện chưa định nghĩa bất kỳ class hoặc hàm nào. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

*Hiện tại chưa có bất kỳ giao diện công khai nào được triển khai trong package này.*

Giao diện kế hoạch được quy định trong [`SPEC-LLM-001.md`](../../../../../docs/04-module-specs/model-gateway/SPEC-LLM-001.md):
```python
# Giao diện mục tiêu đề xuất
class ModelGatewayClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        model_name: str,
        temperature: float = 0.0,
        response_schema: dict[str, Any] | None = None
    ) -> ModelCompletionResult: ...
    
    async def stream_tokens(self, messages: list[dict[str, str]], model_name: str) -> AsyncIterator[str]: ...
```

---

## 6. Luồng xử lý (Processing Flow)

*Hiện chưa có luồng xử lý thực thi nào được cài đặt.*

Quy trình điều phối mô hình theo thiết kế:
```
Node đồ thị Agent ──► ModelGatewayClient ──► Kiểm tra hạn mức Token ──► Client nhà cung cấp (OpenAI/Gemini) ──► Hạch toán Token ──► Kết quả cấu trúc
```

---

## 7. Phụ thuộc & Tương tác liên module

- Dự kiến được gọi bởi các node Agent bên trong `application/state_graph.py` hoặc `agents/`.
- Dự kiến tích hợp với `context/circuit_breaker.py` và `context/fallback_ladder.py` để thực hiện chuyển tầng dự phòng khi mô hình chính gặp sự cố.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

- Cách ly khóa bí mật: Các API key của mô hình tuyệt đối không được ghi vào log hoặc in ra trong các thuộc tính của vết truy vết tracing.
- Kiểm soát kết nối luồng ra: Các yêu cầu gọi mô hình chỉ được gửi tới các endpoint đã phê duyệt, ngăn chặn tấn công SSRF luồng ra khi proxy tới các mô hình nội bộ.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- Phi trạng thái: Hoạt động như một bộ chuyển đổi giao thức phi trạng thái.
- Khả năng chống chịu giới hạn tốc độ (Rate Limit Resilience): Phản hồi `429 Too Many Requests` từ nhà cung cấp phải được bắt và thử lại với thời gian chờ lũy thừa (exponential backoff) trước khi chuyển sang nhà cung cấp thay thế thông qua `context/fallback_ladder.py`.

---

## 10. Kiểm thử & Gỡ lỗi

### Trạng thái kiểm thử
```
Không tìm thấy bài test nào cho package llm/.
```

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: `llm/` hoàn toàn rỗng. Nền tảng hiện chưa có một client Model Gateway sống nào được kết nối vào `agent-runtime`.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-LLM-001.md`](../../../../../docs/04-module-specs/model-gateway/SPEC-LLM-001.md)
- Điều kiện cập nhật: Bổ sung nội dung khi triển khai các adapter client kết nối mô hình (LiteLLM, OpenAI SDK, hoặc REST adapter trực tiếp).
