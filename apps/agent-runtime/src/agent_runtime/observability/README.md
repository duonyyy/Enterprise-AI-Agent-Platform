# `observability/` — Động cơ truy vết phân tán OpenTelemetry 11 Span

> **Trạng thái:** `PARTIAL` (Lan truyền traceparent chuẩn W3C, enum 11 span vòng đời, context manager và ngăn xếp span đã triển khai; bộ xuất dữ liệu OTLP sang Jaeger/Collector và các metrics Prometheus chưa có)  
> **Mã nguồn đã rà soát:** [`observability/tracer.py`](tracer.py), [`observability/__init__.py`](__init__.py)  
> **Tài liệu SPEC liên quan:** [`OBSERVABILITY_GUIDE.md`](../../../../../docs/08-operations/OBSERVABILITY_GUIDE.md), [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md)

---

## 1. Mục đích

Package `observability/` cung cấp khả năng lan truyền ngữ cảnh truy vết phân tán (distributed tracing) và quản lý các span xuyên suốt vòng đời thực thi của Agent. Module này tuân thủ đặc tả ngữ cảnh truy vết chuẩn W3C (chuẩn header `traceparent`) và đo lường chi tiết 11 giai đoạn thực thi chuẩn hóa của nền tảng.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm của module:**
- Phân tích và sinh header `traceparent` theo chuẩn W3C (`00-{trace_id}-{span_id}-01`).
- Quản lý ngăn xếp span (span stack) trong cùng luồng và theo dõi quan hệ lồng nhau giữa span cha và span con.
- Đo lường thời gian thực thi của span với độ chính xác micro-giây bằng `time.perf_counter()`.
- Ghi nhận các thuộc tính ngữ cảnh thực thi (attributes), trạng thái span (`OK` / `ERROR`), và thông điệp lỗi khi xảy ra sự cố.

**Phạm vi không thuộc về module:**
- Ghi nhật ký kiểm toán nghiệp vụ bền vững (thuộc thẩm quyền của `governance.audit_events` và `audit_ledger.py`).
- Xuất dữ liệu nhạy cảm thô hoặc thông tin PII trong các thuộc tính span (thuộc tính bắt buộc phải được làm sạch trước khi ghi nhận).
- Cảnh báo tự động, bảng điều khiển giám sát (dashboards), hoặc tổng hợp log hạ tầng.

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Phân tích Header Traceparent W3C | `IMPLEMENTED` | [`observability/tracer.py::AgentTracer.__init__`](tracer.py#L66) |
| Sinh Header Traceparent W3C | `IMPLEMENTED` | [`observability/tracer.py::AgentTracer.generate_traceparent`](tracer.py#L74) |
| Enum 11 Span chuẩn hóa | `IMPLEMENTED` | [`observability/tracer.py::SpanName`](tracer.py#L20) |
| Trình quản lý ngữ cảnh Span (Context Manager) | `IMPLEMENTED` | [`observability/tracer.py::AgentTracer.start_span`](tracer.py#L78) |
| Lồng ghép Span Cha - Con | `IMPLEMENTED` | [`observability/tracer.py::AgentTracer.start_span`](tracer.py#L85) |
| Bộ xuất OTLP HTTP/gRPC Exporter | `NOT_IMPLEMENTED` | Các span hiện chỉ lưu trong danh sách bộ nhớ RAM (`self.spans`) |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`tracer.py`](tracer.py) | `SpanName` | Enum biểu diễn 11 span chuẩn hóa xuyên suốt vòng đời thực thi của Agent. |
| [`tracer.py`](tracer.py) | `SpanRecord` | Dataclass lưu `trace_id`, `span_id`, `parent_span_id`, thời lượng, trạng thái, và siêu dữ liệu. |
| [`tracer.py`](tracer.py) | `AgentTracer` | Trình quản lý ngữ cảnh xử lý trace ID W3C, xếp chồng span cha, và hoàn tất span. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

### Danh sách 11 Span chuẩn hóa (`SpanName`)
1. `http.ingress`: Điểm tiếp nhận yêu cầu HTTP/SSE ngoài cùng.
2. `auth.tenant_verify`: Giải mã JWT và xác thực tenant/workspace.
3. `policy.pep_check`: Đánh giá tại Điểm thực thi chính sách (PEP).
4. `guardrail.pii_scrub`: Làm sạch PII đầu vào và chuẩn hóa ký tự Unicode.
5. `context.semantic_cache`: Tra cứu và đánh giá bộ đệm ngữ nghĩa.
6. `context.assemble`: Lắp ráp cửa sổ ngữ cảnh và tính toán ngân sách token.
7. `model.gateway_call`: Gọi và phát luồng từ nhà cung cấp mô hình AI.
8. `tool.dispatcher_step`: Thực thi 12 bước của Cổng kiểm soát thực thi công cụ (Tool Gateway).
9. `db.state_checkpoint`: Lưu điểm trạng thái bền vững vào cơ sở dữ liệu PostgreSQL.
10. `event.publish`: Phát sự kiện ra outbox hoặc tới các client SSE đang đăng ký.
11. `guardrail.output_filter`: Lọc nội dung phản hồi mô hình và xác minh trích dẫn.

### Ví dụ sử dụng Tracer
```python
tracer = AgentTracer(traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")

with tracer.start_span(SpanName.TOOL_DISPATCHER_STEP, attributes={"tool_id": "calc"}) as span:
    # Thực hiện lệnh gọi công cụ
    pass

outbound_header = tracer.generate_traceparent()
```

---

## 6. Luồng xử lý (Processing Flow)

### Vòng đời của Span & Lan truyền ngữ cảnh
```mermaid
sequenceDiagram
    participant Ingress as Ingress API
    participant Tracer as AgentTracer
    participant Span as SpanRecord
    participant Downstream as Downstream Tool / Model

    Ingress->>Tracer: AgentTracer(traceparent=req.headers["traceparent"])
    Note over Tracer: Phân tích trace_id và root_parent_span_id
    
    Ingress->>Tracer: with tracer.start_span("http.ingress")
    Tracer->>Span: Tạo span gốc (parent=root, start=perf_counter())
    
    subgraph Span Con Lồng Nhau (Nested Span)
        Ingress->>Tracer: with tracer.start_span("tool.dispatcher_step")
        Tracer->>Span: Tạo span con (parent=http.ingress.span_id)
        Tracer->>Downstream: Truyền header generate_traceparent()
        Downstream-->>Tracer: Phản hồi kết quả
        Note over Span: Kết thúc span con (status="OK", duration_ms)
    end

    Note over Span: Kết thúc span gốc
    Tracer-->>Ingress: Toàn bộ span được gom lại trong tracer.spans
```

---

## 7. Phụ thuộc & Tương tác liên module

- **Thư viện tiêu chuẩn:** Xây dựng thuần túy từ các module tiêu chuẩn của Python (`contextlib`, `secrets`, `re`, `time`).
- **Các điểm tích hợp:**
  - `agent_runtime.tools.dispatcher`: Các lệnh gọi nhận `trace_id` và chuyển sang các sự kiện kiểm toán.
  - `agent_runtime.api.runs`: Các lần chạy ghi nhận `trace_id` vào bảng CSDL `runtime.runs`.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

1. **Không chứa dữ liệu nhạy cảm trong thuộc tính:** `SpanRecord.attributes` tuyệt đối không được lưu mật khẩu người dùng dạng thô, PII chưa che giấu, hoặc Bearer token API.
2. **Nhãn an toàn (Safe Labels):** Tenant ID và workspace ID được phép ghi lại làm nhãn (tags) của span, nhưng phần thân yêu cầu (request body) bắt buộc phải được làm sạch.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- **Lưu trữ trong bộ nhớ:** Hiện tại, các span được tích lũy trong danh sách nội bộ tiến trình (`self.spans`).
- **Ghi nhận lỗi:** Nếu có ngoại lệ chưa xử lý xảy ra bên trong khối `start_span`, khối lệnh sẽ bắt ngoại lệ, đánh dấu `status="ERROR"`, ghi lại `error_message=str(exc)`, và ném lại ngoại lệ ra ngoài để luồng xử lý không bị nuốt lỗi.

---

## 10. Kiểm thử & Gỡ lỗi

### Các lệnh kiểm thử thực tế đã xác minh
```powershell
# Chạy bộ test hợp đồng truy vết OpenTelemetry
python -m pytest tests/contract/test_opentelemetry_tracing.py -v
```
*Bằng chứng thực tế*: **3 passed in 0.11s**.

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `CODE_GAP`: Các span chưa được đẩy ra OpenTelemetry Collector, Jaeger, hoặc Grafana Tempo qua các bộ xuất chuẩn OTLP HTTP/gRPC.
- `CODE_GAP`: Chưa có cơ chế thu thập metrics giám sát (bộ đếm Prometheus, histogram đo độ trễ thực thi công cụ).

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Hướng dẫn giám sát vận hành: [`OBSERVABILITY_GUIDE.md`](../../../../../docs/08-operations/OBSERVABILITY_GUIDE.md)
- Đặc tả kiến trúc: [`SPEC-RUNTIME-001.md`](../../../../../docs/04-module-specs/runtime/SPEC-RUNTIME-001.md)
- Điều kiện cập nhật: Cập nhật tài liệu này khi đăng ký thêm OTLP exporter, bổ sung các thuộc tính span mới, hoặc tích hợp các metrics Prometheus.
