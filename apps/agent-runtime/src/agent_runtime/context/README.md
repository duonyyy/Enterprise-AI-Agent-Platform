# `context/` — Tam giác độ tin cậy (Circuit Breaker, Fallback Ladder & Semantic Cache)

> **Trạng thái:** `PARTIAL` (Tam giác độ tin cậy đã triển khai; việc lắp ráp ngữ cảnh prompt giới hạn và tính toán ngân sách token cho LLM hiện chưa có)  
> **Mã nguồn đã rà soát:** [`context/circuit_breaker.py`](circuit_breaker.py), [`context/fallback_ladder.py`](fallback_ladder.py), [`context/semantic_cache.py`](semantic_cache.py), [`context/__init__.py`](__init__.py)  
> **Tài liệu SPEC liên quan:** [`SPEC-MEMORY-001.md`](../../../../../docs/04-module-specs/memory/SPEC-MEMORY-001.md), [`FINOPS_GPU_CALCULATOR.md`](../../../../../docs/08-operations/FINOPS_GPU_CALCULATOR.md)

---

## 1. Mục đích

Package `context/` hiện chứa **Tam giác độ tin cậy** (Reliability Triangle - Track 2 Day 25) của nền tảng: một bộ ngắt mạch 3 pha (Circuit Breaker) ngăn chặn sự cố sụp đổ dây chuyền, một thang dự phòng 5 tầng (Fallback Ladder) kèm cơ chế kiểm soát chất lượng đầu ra đảm bảo hệ thống suy thoái êm dịu (graceful degradation), và một bộ đệm vector ngữ nghĩa (Semantic Vector Cache) phản hồi câu hỏi dưới một mili-giây với chi phí gọi mô hình bằng 0.

---

## 2. Trách nhiệm & Phạm vi không thuộc về module (Non-goals)

**Trách nhiệm của module:**
- Ngắt nhanh (fast-fail) các lệnh gọi tới dịch vụ downstream không khỏe mạnh thông qua cơ chế ngắt mạch 3 pha (`CLOSED`, `OPEN`, `HALF_OPEN`).
- Tính toán thời gian chờ lũy thừa ngẫu nhiên hoàn toàn (Full Jitter Exponential Backoff).
- Thực thi chuyển tầng dự phòng tuần tự qua các cấp mô hình/nhà cung cấp với cơ chế tự động thẩm định chất lượng đầu ra.
- Đối sánh bộ đệm ngữ nghĩa bằng khoảng cách Cosine trên tần suất từ (ngưỡng mặc định 90%) kèm cơ chế loại bỏ FIFO và thời hạn hiệu lực (TTL).

**Phạm vi không thuộc về module:**
- Bộ nhớ hội thoại dài hạn hoặc hồ sơ cá nhân của người dùng xuyên suốt các lần chạy (được giao cho `memory/`).
- Lưu trữ vector chính hoặc embeddings cho hệ thống RAG (được giao cho `knowledge/`).
- Quản lý API key nhà cung cấp hoặc kết nối mạng trực tiếp tới model (được giao cho `llm/`).

---

## 3. Trạng thái triển khai

| Khả năng | Trạng thái | Bằng chứng mã nguồn |
|---|---|---|
| Bộ ngắt mạch 3 pha (Circuit Breaker) | `IMPLEMENTED` | [`context/circuit_breaker.py::CircuitBreaker`](circuit_breaker.py#L23) |
| Chờ lũy thừa ngẫu nhiên (Full Jitter Backoff) | `IMPLEMENTED` | [`context/circuit_breaker.py::CircuitBreaker.compute_backoff`](circuit_breaker.py#L80) |
| Thang dự phòng 5 tầng (Fallback Ladder) | `IMPLEMENTED` | [`context/fallback_ladder.py::FallbackLadder`](fallback_ladder.py#L14) |
| Kiểm soát chất lượng đầu ra | `IMPLEMENTED` | [`context/fallback_ladder.py::FallbackLadder`](fallback_ladder.py#L33) |
| Bộ đệm vector ngữ nghĩa (Semantic Cache) | `IMPLEMENTED` | [`context/semantic_cache.py::SemanticVectorCache`](semantic_cache.py#L12) |
| Lắp ráp ngữ cảnh prompt & Quản lý Token | `NOT_IMPLEMENTED` | Chức năng theo đặc tả SPEC về nén tin nhắn/ngân sách token chưa có |

---

## 4. Sơ đồ File và Ký hiệu (Symbol Map)

| Tệp tin | Ký hiệu (Symbol) | Trách nhiệm |
|---|---|---|
| [`circuit_breaker.py`](circuit_breaker.py) | `CircuitState` | Enum trạng thái ngắt mạch: `CLOSED`, `OPEN`, `HALF_OPEN`. |
| [`circuit_breaker.py`](circuit_breaker.py) | `CircuitBreakerOpenException` | Ngoại lệ ném ra khi cố gắng thực thi lệnh gọi trên một mạch đang mở (`OPEN`). |
| [`circuit_breaker.py`](circuit_breaker.py) | `CircuitBreaker` | Theo dõi ngưỡng lỗi và cửa sổ thời gian hồi phục để ngắt nhanh các cuộc gọi trong < 0.1ms. |
| [`fallback_ladder.py`](fallback_ladder.py) | `FallbackLadder` | Lặp tuần tự qua các hàm của từng tầng cho đến khi kết quả vượt qua hàm callback thẩm định chất lượng. |
| [`fallback_ladder.py`](fallback_ladder.py) | `QualityGuardrailFailed` | Ngoại lệ ném ra khi kết quả của tầng không đạt ngưỡng chất lượng hoặc tính trung thực (faithfulness). |
| [`semantic_cache.py`](semantic_cache.py) | `SemanticVectorCache` | Vectorizer tần suất từ và đối sánh độ tương đồng Cosine trong bộ nhớ RAM kèm cơ chế hết hạn TTL. |

---

## 5. Giao diện công khai & Hợp đồng dữ liệu (Interfaces & Contracts)

### Chữ ký các thành phần
```python
# 1. Bộ ngắt mạch (Circuit Breaker)
cb = CircuitBreaker(failure_threshold=3, recovery_time_seconds=4.0, half_open_success_threshold=2)
result = cb.call(func, *args, **kwargs)
delay = cb.compute_backoff(attempt=2, base_delay=0.5, max_delay=8.0)

# 2. Thang dự phòng (Fallback Ladder)
ladder = FallbackLadder(tiers=[("primary", fn1), ("secondary", fn2)], validator=quality_fn)
outcome = ladder.execute_with_fallback(*args, **kwargs)
# outcome: {"success": bool, "resolved_tier": str, "result": Any, "fallbacks_attempted": list}

# 3. Bộ đệm vector ngữ nghĩa (Semantic Vector Cache)
cache = SemanticVectorCache(similarity_threshold=0.90, max_entries=10000, ttl_seconds=86400.0)
answer, score, latency_ms = cache.lookup(query: str)
cache.store(query: str, response: str)
```

---

## 6. Luồng xử lý (Processing Flow)

### Thực thi qua Tam giác độ tin cậy
```mermaid
flowchart TD
    Query["Câu hỏi từ người dùng"] --> CacheCheck["SemanticVectorCache.lookup(query)"]
    CacheCheck -->|Cosine >= 0.90 & Chưa hết hạn| CacheHit["Trả về kết quả từ Cache (< 2ms, $0)"]
    
    CacheCheck -->|Cache Miss (Không thấy)| Ladder["FallbackLadder.execute_with_fallback()"]
    
    subgraph Thực thi các tầng qua Circuit Breaker
        Ladder --> Tier1["Tầng 1: Mô hình hiệu năng cao"]
        Tier1 --> CB1{"CircuitBreaker.call()"}
        CB1 -->|Mạch Mở / Lỗi| Tier2["Tầng 2: Mô hình tối ưu chi phí"]
        Tier2 --> CB2{"CircuitBreaker.call()"}
        CB2 -->|Mạch Mở / Lỗi| SafeDegrade["Thông báo suy thoái an toàn tất định"]
    end

    CB1 -->|Thành công & Đạt chất lượng| StoreCache["SemanticVectorCache.store(query, res)"]
    CB2 -->|Thành công & Đạt chất lượng| StoreCache
    StoreCache --> ReturnRes["Trả kết quả cho người dùng"]
```

---

## 7. Phụ thuộc & Tương tác liên module

- **Triển khai siêu nhẹ:** Hoạt động hoàn toàn bằng các module Python thuần `math`, `random`, `re`, `time`.
- **Điểm tích hợp điều phối:** Được thiết kế để bọc các lệnh gọi Model Gateway bên trong `application/state_graph.py` và `llm/`.

---

## 8. Ranh giới Bảo mật, Phân vùng Tenant & Chính sách

1. **Khoảng trống phân vùng Tenant trong Cache:** `SemanticVectorCache` hiện là một danh sách unpartitioned trong bộ nhớ RAM. Việc lưu cache câu hỏi giữa các tenant mà không phân vùng theo `workspace_id` có nguy cơ rò rỉ dữ liệu.
2. **Thực thi chất lượng:** `FallbackLadder` chủ động từ chối các kết quả ảo giác (hallucination) hoặc có độ trung thực thấp thông qua callback `validator` do đối tượng gọi cung cấp.

---

## 9. Trạng thái, Tác dụng phụ (Side Effects) & Khôi phục sự cố

- **Cam kết không sập hệ thống (Zero-Crash Guarantee):** `FallbackLadder.execute_with_fallback()` bắt toàn bộ các ngoại lệ và bảo đảm luôn giải quyết về tầng `"safe_deterministic_degradation"` với `"success": False` thay vì để sập luồng xử lý của tiến trình.
- **Tự động phục hồi trạng thái mạch:** Sau khi hết thời gian `recovery_time_seconds`, `CircuitBreaker` chuyển sang trạng thái thăm dò `HALF_OPEN` để kiểm tra lại sức khỏe dịch vụ downstream.

---

## 10. Kiểm thử & Gỡ lỗi

### Các lệnh kiểm thử thực tế đã xác minh
```powershell
# Chạy bộ test hợp đồng Tam giác độ tin cậy
python -m pytest tests/contract/test_reliability_triangle.py -v
```
*Bằng chứng thực tế*: **4 passed in 0.14s**.

---

## 11. Các khoảng trống kỹ thuật & Nợ kỹ thuật (Known Gaps / Technical Debt)

- `ARCHITECTURE_GAP`: Theo đặc tả `SPEC-MEMORY-001`, module `context/` phải chịu trách nhiệm chọn lọc tin nhắn, tính toán ngân sách token và lắp ráp cửa sổ ngữ cảnh (context window). Tuy nhiên, hiện tại module này lại chứa các thành phần phục hồi hạ tầng. Logic lắp ráp ngữ cảnh prompt thực tế đang bị thiếu.
- `CODE_GAP`: `SemanticVectorCache` chưa phân vùng các mục lưu đệm theo `tenant_id` và `workspace_id`.
- `CODE_GAP`: Phương thức `SemanticVectorCache._text_to_vector` sử dụng đếm tần suất từ đơn giản thay vì mô hình dense embeddings đa ngôn ngữ.

---

## 12. Tài liệu liên quan & Quy tắc bảo trì

- Mục lục gốc: [`agent_runtime/README.md`](../README.md)
- Đặc tả kiến trúc: [`SPEC-MEMORY-001.md`](../../../../../docs/04-module-specs/memory/SPEC-MEMORY-001.md)
- Hướng dẫn vận hành: [`FINOPS_GPU_CALCULATOR.md`](../../../../../docs/08-operations/FINOPS_GPU_CALCULATOR.md)
- Điều kiện cập nhật: Cập nhật tài liệu này khi chuyển các thành phần phục hồi sang package hạ tầng chuyên biệt hoặc triển khai logic quản lý token ngữ cảnh prompt.
