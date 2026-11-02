# EVENT SPECIFICATIONS — STREAMING & DISTRIBUTED EVENT BUS

> **Hệ thống:** Enterprise AI Agent Platform  
> **Kiến trúc:** Dual-Tier Event Delivery (Client SSE Stream + Enterprise Apache Kafka Event Bus)  
> **Quy chuẩn:** CloudEvents v1.0, W3C Server-Sent Events, ADR-014 Pluggable Event Bus  
> **Phạm vi:** Runtime Timeline, Outbox Events, Knowledge Pipeline, Telemetry & Multi-Agent

---

## PHẦN I: CLIENT-SIDE STREAMING (SERVER-SENT EVENTS - SSE)

Mọi tiến trình chạy Agent kết nối với Web Console đều phát luồng sự kiện Server-Sent Events theo định dạng:

```text
id: <sequence_number>
event: <event_type>
data: {"runId": "<uuid>", "sequence": <int>, "payload": {...}}
```

### 1. Danh mục Event Types phía Client:
- **Vòng đời Chạy:** `run.created`, `run.started`, `step.started`, `run.resumed`, `run.succeeded`, `run.failed`
- **Mô hình & Suy luận:** `model.requested`, `model.streaming_chunk`, `model.completed`
- **Kỹ năng & Khám phá:** `skill.discovered`, `skill.loaded`
- **Công cụ & Thực thi:** `tool.requested`, `tool.succeeded`, `tool.failed`
- **Phê duyệt Con người:** `approval.requested`, `approval.decided`

---

## PHẦN II: ENTERPRISE DISTRIBUTED EVENT BUS (APACHE KAFKA)

Theo quyết định kiến trúc [ADR-014](../02-architecture/adr/ADR-014-pluggable-event-bus-kafka.md), hệ thống hỗ trợ truyền phát sự kiện phân tán qua Apache Kafka tại **4 vị trí trọng yếu**.

### 1. Quy ước Đặt tên Topic (Topic Naming Convention)
Mọi topic đều tuân theo cấu trúc phân cấp:
$$\{environment\} . \{domain\} . \{entity\} . \{event\_type\}$$

Ví dụ: `prod.agent.run.events`, `dev.knowledge.ingest.jobs`.

---

### 2. Danh mục 4 Cụm Topic Chiến lược

| Vị trí Tích hợp | Tên Topic Chính | Topic Dead-Letter (DLQ) | Partition Key | Tần suất & Mục đích |
|---|---|---|---|---|
| **Vị trí 1: Outbox Event Streamer** | `{env}.agent.run.events`<br>`{env}.agent.tool.dispatched` | `{env}.agent.run.events.dlq` | `tenant_id:workspace_id` | **Cao (Real-time):** Bắn timeline từng bước chạy và lệnh gọi công cụ cho các service tiêu thụ. |
| **Vị trí 2: Knowledge Ingestion** | `{env}.knowledge.ingest.jobs` | `{env}.knowledge.ingest.jobs.dlq` | `tenant_id:workspace_id` | **Trung bình (Batch):** Phân tải việc nạp tài liệu RAG, chunking, embedding cho cụm `jobs-worker`. |
| **Vị trí 3: WORM Audit & FinOps** | `{env}.telemetry.audit.stream` | `{env}.telemetry.audit.stream.dlq` | `tenant_id` | **Rất cao (Streaming):** Hấp thụ nhật ký kiểm toán, chi phí token, ghi nhận batch vào CSDL. |
| **Vị trí 4: Multi-Agent Choreography** | `{env}.agent.choreography.dispute.new`<br>`{env}.agent.choreography.dispute.evidence`<br>`{env}.agent.choreography.dispute.ruling` | `{env}.agent.choreography.dispute.dlq` | `case_id` | **Nghiệp vụ:** Điều phối giao tiếp bất đồng bộ giữa các tác tử chuyên viên (như Hội đồng Olist). |

---

### 3. Cấu trúc Message Chuẩn Hóa (CloudEvents v1.0 Schema)

Mọi thông điệp gửi vào Kafka bắt buộc phải tuân theo cấu trúc CloudEvents v1.0:

```json
{
  "specversion": "1.0",
  "id": "evt-7f9a2b4c-8d1e-4f2a-b3c4-9e8d7c6b5a4f",
  "source": "/enterprise/agent-runtime/node-01",
  "type": "enterprise.agent.run.step.v1",
  "datacontenttype": "application/json",
  "time": "2026-11-01T22:30:00.123Z",
  "tenant_id": "ten-9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
  "workspace_id": "ws-1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
  "correlation_id": "corr-4f5e6d7c-8b9a-0f1e-2d3c-4b5a6f7e8d9c",
  "data": {
    "run_id": "run-001",
    "step_id": "step-003",
    "node_name": "tool_execution_node",
    "tool_key": "catalog.sql_runner@1.0.0",
    "operation_key": "op-kltn-2026-001",
    "status": "SUCCEEDED",
    "duration_ms": 142,
    "tokens_used": {
      "prompt_tokens": 450,
      "completion_tokens": 85,
      "cost_usd": 0.00045
    }
  }
}
```

---

### 4. Chiến lược Phân vùng & Bảo toàn Thứ tự (Partitioning & Ordering)

- **Nguyên tắc FIFO tuyệt đối:** Bắt buộc sử dụng `Message Key = f"{tenant_id}:{workspace_id}"`. Kafka bảo đảm tất cả thông điệp có cùng Key sẽ luôn đi vào cùng 1 Partition, loại bỏ hoàn toàn rủi ro đảo lộn thứ tự sự kiện của một phiên chạy Agent.
- **Số lượng Partitions khuyến nghị:**
  - Topic Tải Cao (`agent.run.events`, `telemetry.audit.stream`): Tối thiểu 12 partitions cho phép scale mở rộng cụm Consumer.
  - Topic Nghiệp vụ (`knowledge.ingest.jobs`, `agent.choreography.*`): Tối thiểu 6 partitions.
- **Chính sách Lưu trữ (Retention Policy):**
  - Event Stream (`agent.run.events`): 7 ngày (168 giờ).
  - Ingestion Jobs (`knowledge.ingest.jobs`): 3 ngày (72 giờ) kết hợp cơ chế nén log (Log Compaction).
  - Audit Stream (`telemetry.audit.stream`): 30 ngày (sau khi đã batch insert vào CSDL / OpenSearch).

---

### 5. Chính sách Xử lý Sự cố & Dead-Letter Queue (DLQ)

```text
Message Ingestion
       │
       ▼
 Consumer Try 1 ──► [Lỗi] ──► Chờ 1s (Backoff)
       │
       ▼
 Consumer Try 2 ──► [Lỗi] ──► Chờ 2s (Backoff)
       │
       ▼
 Consumer Try 3 ──► [Lỗi] ──► Chờ 4s (Backoff)
       │
       ▼
 Chuyển sang Topic DLQ (*.dlq) + Phát chuông cảnh báo P1 (Slack/PagerDuty)
```

1. **Max Retries:** 3 lần với khoảng cách thời gian tăng lũy thừa (Exponential Backoff: 1s, 2s, 4s kèm random jitter).
2. **Dead-Letter Queue:** Nếu sau 3 lần vẫn lỗi (do payload sai schema, lỗi kết nối cơ sở dữ liệu), thông điệp tự động đẩy sang Topic `.dlq` tương ứng kèm header `x-exception-cause` và `x-failed-timestamp`.
3. **Graceful Fallback:** Khi cụm Kafka Broker ngoại tuyến hoặc mất kết nối, hệ thống tự động rơi về chế độ an toàn ghi sự kiện vào bảng PostgreSQL `integration.outbox_events` (không làm sập phiên làm việc của người dùng).
