# RUNBOOK: DISASTER RECOVERY & SRE RESILIENCE
**Enterprise AI Agent Platform — Production SRE Standard (VinAI Capstone Track 2 Day 23 & Google SRE Postmortem)**

---

## 1. Mục Tiêu và Chỉ Số Cốt Lõi (SLA / SLO Targets)

Hệ thống được thiết kế theo tiêu chuẩn sẵn sàng cao (High Availability) phục vụ quy mô Enterprise đa người thuê (Multi-Tenant) với các cam kết sau:

| Chỉ số | Định nghĩa | Ngưỡng cam kết (Target) | Cơ chế bảo đảm |
| :--- | :--- | :--- | :--- |
| **RTO** *(Recovery Time Objective)* | Thời gian tối đa để phục hồi dịch vụ sau sự cố sập toàn diện | **$< 30$ giây** | Tự động chuyển mạch DNS sang Warm Standby Replica |
| **RPO** *(Recovery Point Objective)* | Lượng dữ liệu tối đa chấp nhận mất mát tính theo thời gian | **$< 60$ giây** | PostgreSQL Streaming Replication & Outbox Events |
| **Availability (SLA)** | Tỷ lệ thời gian hệ thống vận hành bình thường | **99.95%** (downtime $< 21.9$ phút/tháng) | Multi-AZ K8s Pods, Active-Passive DB replication |
| **Data Integrity** | Tính toàn vẹn chống chỉnh sửa lén của lịch sử kiểm toán | **100% Cryptographic Proof** | Tamper-Evident SHA-256 Hash Chaining (`governance.audit_events`) |

---

## 2. Kiến Trúc Sẵn Sàng Cao (HA Architecture Topology)

```
                            [ Cloudflare / Route53 DNS ]
                           /                           \
                  Primary Region                     Secondary Region
               (Active - 99% Traffic)              (Warm Standby - 1% Traffic)
             ┌─────────────────────────┐          ┌─────────────────────────┐
             │ Ingress ALB             │          │ Ingress ALB             │
             │   ├── Control API Pods  │          │   ├── Control API Pods  │
             │   └── Agent Runtime Pods│          │   └── Agent Runtime Pods│
             │                         │          │                         │
             │ PostgreSQL 16 (Leader)  │──WAL────>│ PostgreSQL 16 (Follower)│
             │ Redis Sentinel (Master) │──Sync───>│ Redis Sentinel (Replica)│
             │ Kafka Cluster (Active)  │──Mirror─>│ Kafka Cluster (Standby) │
             └─────────────────────────┘          └─────────────────────────┘
```

---

## 3. Phân Biệt Bản Chất Sâu Sắc: SIGKILL vs SIGSTOP

Trong môi trường vận hành microservices và cụm tính toán GPU/LLM, việc hiểu sai trạng thái chết của tiến trình là nguyên nhân hàng đầu dẫn đến **sự cố dây chuyền (Cascading Failure)**:

### 3.1. Phân tích so sánh kỹ thuật

```
+-----------------------------------------------------------------------------------------+
|                  SO SÁNH BẢN CHẤT HỆ THỐNG: SIGKILL VS SIGSTOP                           |
+-----------------------------------------------------------------------------------------+
| Tiêu chí          | SIGKILL (Signal 9)              | SIGSTOP (Signal 19 / Freeze)      |
+-------------------+---------------------------------+-----------------------------------+
| Hành vi Kernel    | Thu hồi toàn bộ RAM/VRAM ngay   | Tạm dừng lập lịch CPU, giữ nguyên |
|                   | đóng socket TCP bằng gói FIN/RST| trạng thái bộ nhớ và socket TCP   |
+-------------------+---------------------------------+-----------------------------------+
| Phía Client / API | Nhận ngay lỗi 'Connection Reset'| Socket vẫn ở trạng thái ESTABLISHED|
| Gateway           | trong < 1ms, failover tức thì   | Request bị treo (hung) vô thời hạn|
+-------------------+---------------------------------+-----------------------------------+
| Hậu quả hệ thống  | Sạch sẽ, không gây nghẽn        | CONNECTION POOL EXHAUSTION: Toàn bộ|
|                   |                                 | worker bị chiếm giữ -> sập gateway|
+-------------------+---------------------------------+-----------------------------------+
| Tình huống thực tế| K8s OOMKilled, Pod termination  | GPU vLLM CUDA Deadlock, Docker    |
|                   |                                 | Container paused, swap thrashing  |
+-----------------------------------------------------------------------------------------+
```

### 3.2. Cơ chế phòng thủ bắt buộc (Anti-Freeze Safeguards)
1. **TCP Keepalive ở cấp độ Socket:**
   - Cấu hình OS/Client: `SO_KEEPALIVE=1`, `tcp_keepalive_time=15`, `tcp_keepalive_intvl=5`, `tcp_keepalive_probes=3`.
2. **Deep Healthcheck Timeout Isolation:**
   - Mọi probe kiểm tra trạng thái downstream (`HealthChecker`) phải được bọc trong luồng riêng với timeout nghiêm ngặt **500ms** (như đã cài đặt tại `apps/agent-runtime/src/agent_runtime/api/health.py`). Nếu tiến trình bị `SIGSTOP`, probe lập tức timeout và đánh dấu `failed` thay vì treo toàn bộ endpoint `/health`.
3. **HTTP Client Timeout Hierarchy:**
   - `Connect Timeout`: $1.0\text{s}$
   - `Read Timeout`: $15.0\text{s}$ (với Streaming SSE: first byte $5.0\text{s}$, chunk $2.0\text{s}$)
   - `Pool Timeout`: $2.0\text{s}$

---

## 4. Kịch Bản Diễn Tập Phục Hồi Thảm Họa (Disaster Recovery Drill Runbook)

### Bước 1: Phát hiện và xác nhận sự cố (T0 - 0 đến 10 giây)
- Deep Healthcheck tại Primary Region trả về `status: "not_ready"` trên 3 lần thăm dò liên tiếp (mỗi lần cách nhau 3 giây).
- Đội ngũ SRE trực nhận cảnh báo P0 PagerDuty.

### Bước 2: Chuyển mạch Standby Database (T1 - 10 đến 20 giây)
- Thực hiện lệnh promote PostgreSQL Follower tại Secondary Region trở thành Leader:
  ```bash
  # Thực thi tại cụm Secondary DB
  pg_ctl promote -D /var/lib/postgresql/data
  ```
- Kiểm tra tính toàn vẹn 56 bảng và hash chain kiểm toán:
  ```bash
  python -c "from platform_common.audit_ledger import TamperEvidentLedger; print('DB Promoted Successfully')"
  ```

### Bước 3: Chuyển mạch Ingress DNS (T2 - 20 đến 30 giây)
- Cập nhật Route53 / Cloudflare Weighted DNS:
  - `Primary Region Weight`: $0\%$
  - `Secondary Region Weight`: $100\%$
- Cắt đứt kết nối mạng cũ của Primary để phòng ngừa hiện tượng não đôi (Split-Brain).

### Bước 4: Tự động Resume trạng thái Agent (LangGraph Checkpoint Recovery)
- Các Agent đang chạy dở sẽ không bị mất dữ liệu:
  - Runtime mới đọc `runtime.checkpoint_refs` từ PostgreSQL Secondary.
  - Tải checkpoint hợp lệ cuối cùng và tiếp tục thực thi bước tiếp theo mà không lặp lại các tác vụ Tool đã hoàn tất (nhờ `runtime.tool_operations` Idempotency Key).

---

## 5. Mẫu Báo Cáo Sự Cố Chuẩn Google SRE (Postmortem Template)

Khi xảy ra sự cố cấp P0/P1, người phụ trách ứng cứu phải hoàn thiện tài liệu Postmortem theo 5 phần bắt buộc sau trong vòng 24 giờ:

```markdown
# INCIDENT POSTMORTEM: [MÃ_SỰ_CỐ - VÍ DỤ INC-20261101-01]

### 1. Tóm tắt điều hành (Executive Summary)
- **Ngày xảy ra:** YYYY-MM-DD | **Thời gian gián đoạn:** XX phút
- **Ảnh hưởng kinh doanh:** XX% người dùng gặp lỗi 503; XX Agent runs bị gián đoạn.
- **Nguyên nhân chính tóm tắt:** GPU vLLM node bị khóa dead-lock CUDA dẫn đến hiện tượng SIGSTOP treo socket, cạn kiệt Connection Pool của Agent Runtime.

### 2. Tác động chi tiết (Impact & Affected Metrics)
- **RTO thực tế:** XX giây (Mục tiêu: < 30s)
- **RPO thực tế:** 0 giây (Không mất mát dữ liệu do WAL sync)
- **Số lượng yêu cầu lỗi (HTTP 5xx):** XX,XXX requests
- **Tổn thất doanh thu / Credits ước tính:** $XXX USD

### 3. Dòng thời gian chi tiết (Incident Timeline - UTC+7)
- **14:02:10** - Bắt đầu xuất hiện spike độ trễ tại Model Gateway (p99 > 15s).
- **14:02:35** - Node GPU gặp CUDA memory stall, rơi vào trạng thái đóng băng SIGSTOP.
- **14:02:45** - Connection pool tại Runtime cạn kiệt, Deep Healthcheck chuyển sang NOT_READY.
- **14:03:00** - Hệ thống tự động kích hoạt Circuit Breaker chuyển trạng thái OPEN.
- **14:03:15** - Tự động failover sang Fallback Ladder Tier 3 (Cloud API).
- **14:04:00** - SRE promote node dự phòng, hệ thống hồi phục hoàn toàn trạng thái READY.

### 4. Phân tích nguyên nhân gốc rễ (Root Cause Analysis - 5 Whys)
1. *Tại sao Runtime không phản hồi?* -> Toàn bộ 50 kết nối database bị chiếm bởi các thread đang chờ Model Gateway.
2. *Tại sao các thread bị treo chờ vô hạn?* -> Model Gateway bị đóng băng socket mà không gửi tín hiệu ngắt TCP FIN.
3. *Tại sao socket không ngắt?* -> Tiến trình container GPU bị Kernel dừng lập lịch nhưng chưa bị SIGKILL.
4. *Tại sao GPU bị dừng?* -> Thiếu cờ cấu hình socket read_timeout và TCP keepalive ở tầng client.
5. *Giải pháp triệt để:* Bổ sung probe timeout 500ms độc lập và Fallback Ladder 5-Tier.

### 5. Hành động khắc phục & Phòng ngừa (Action Items)
| STT | Hành động cụ thể | Mức ưu tiên | Người chịu trách nhiệm | Hạn chót |
| :-- | :--------------- | :---------- | :--------------------- | :------- |
| 1   | Cài đặt Deep Healthcheck 3 trạng thái có bảo vệ SIGSTOP | P0 (Đã xong) | Runtime Team           | Ngay lập tức |
| 2   | Thiết lập TCP keepalive 15s trên toàn bộ K8s Pods     | P1          | DevOps / Infra Team    | 3 ngày |
| 3   | Bổ sung diễn tập Chaos Engineering (Simulate SIGSTOP)   | P2          | QA / SRE Team          | 7 ngày |
```
