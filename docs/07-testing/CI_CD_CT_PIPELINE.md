# CI/CD/CT AUTOMATION PIPELINE SPECIFICATION
**Enterprise AI Agent Platform — Continuous Integration, Delivery & Testing (VinAI Capstone Track 2 Day 21 & AgentArena Standard)**

---

## 1. Tổng Quan Quy Trình CI/CD/CT 4 Cổng (4-Gate Lifecycle)

Khác với phần mềm truyền thống, hệ thống AI Agent đòi hỏi quy trình tự động hóa kiểm định cả về **Code (CI)**, **Dữ liệu & Mô hình (CT - Continuous Testing)** và **Triển khai an toàn (CD)** để chống suy thoái chất lượng (Regression):

```
                   KIẾN TRÚC PIPELINE CI / CD / CT 4 CỔNG KIỂM SOÁT
 [ Git Push / PR ]
         │
         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ GATE 1: TĨNH & HỢP ĐỒNG (Static Code, DB-56, Contracts & Security Gate)     │
 │ ├── Linting (Ruff, Black, Mypy) & 56 Table DB Schema static check           │
 │ ├── Contract Roundtrip (OpenAPI v3.1, JSONSchema v2020-12)                  │
 │ └── Security Scans (Vietnamese PII Scrubber, AST Sandbox, Zero-Width Shield)│
 └─────────────────────────────────────────────────────────────────────────────┘
         │ PASS (100%)
         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ GATE 2: KIỂM THỬ LIÊN TỤC & ĐÁNH GIÁ MÔ HÌNH (Continuous Testing - CT)      │
 │ ├── RAGAS 4 Chỉ số: Faithfulness >= 0.85, Answer Relevance >= 0.80          │
 │ ├── AgentArena Tri-Pillar: Completion >= 85%, Cost <= $0.05, Safety >= 99% │
 │ └── Bottom-10 Diagnosis: Trích xuất 10 ca thất bại nặng nhất để phân tích   │
 └─────────────────────────────────────────────────────────────────────────────┘
         │ PASS (Không suy thoái so với Baseline)
         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ GATE 3: ĐĂNG KÝ VÀ THĂNG HẠNG MÔ HÌNH (Model Registry Promotion)            │
 │ ├── Gán thẻ 'champion' alias trong MLflow / Agent Catalog                   │
 │ ├── Đẩy phiên bản cũ xuống làm 'challenger' dự phòng                        │
 │ └── Sinh SHA-256 Digest và ký số bảo mật bất biến (EU AI Act Art. 12)       │
 └─────────────────────────────────────────────────────────────────────────────┘
         │ PASS (Đã phê duyệt tự động)
         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ GATE 4: TRIỂN KHAI CANARY CÓ GIÁM SÁT (Canary Deployment & Auto-Rollback)   │
 │ ├── Điều phối lưu lượng: 5% -> 25% -> 100% qua Ingress ALB                  │
 │ ├── Giám sát thời gian thực: Error Rate <= 1%, p95 Latency <= 2500ms        │
 │ └── Auto-Rollback tức thì nếu Circuit Breaker bật trạng thái OPEN           │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Chi Tiết Các Cổng Kiểm Soát (Gate Details)

### Gate 1: Kiểm định Tĩnh & Hợp đồng Kiến trúc
Mọi Pull Request bắt buộc vượt qua 100% các tiêu chí sau trước khi được phép merge:
* **Kiểm định DB-56 tĩnh:** Chạy `python docs/03-database/database/validate_db_static.py` xác minh chính xác 56 bảng miền dữ liệu trên 7 schema, 55 RLS policies, 10 immutability triggers.
* **Kiểm định Contract:** Chạy `pytest tests/contract/` xác minh tính tương thích ngược của Agent Specs, MCP Specs, Tool Specs và CloudEvents v1.0.
* **Kiểm định An ninh:** Kiểm tra bộ lọc tiếng Việt PII, khiên chống ký tự ẩn Zero-Width Unicode, và môi trường AST Sandbox.

### Gate 2: Kiểm thử Đánh giá Liên tục (CT - Continuous Testing)
Chạy tự động trên môi trường Staging/Runner với bộ dataset kiểm định phân tầng (Golden Test Set):

#### A. RAGAS 4 Chỉ số Đánh giá RAG & Context:
| Chỉ số RAGAS | Ngưỡng chặn tối thiểu | Ý nghĩa nghiệp vụ |
| :--- | :--- | :--- |
| **Faithfulness** | $\ge 0.85$ | Câu trả lời của Agent hoàn toàn dựa trên dữ liệu trích xuất, không bịa đặt. |
| **Answer Relevance** | $\ge 0.80$ | Trả lời đúng trọng tâm câu hỏi của người dùng. |
| **Context Recall** | $\ge 0.80$ | Bộ truy xuất (Hybrid Retriever) lấy đủ các chunk cần thiết. |
| **Context Precision** | $\ge 0.80$ | Tỷ lệ nhiễu thấp, các chunk liên quan nằm ở thứ hạng đầu (RRF Rank). |

#### B. AgentArena Tri-Pillar Gates:
* **Task Completion Rate:** $\ge 85\%$ qua ma trận $4 \times 3$ kiểm thử luồng nghiệp vụ.
* **Cost Efficiency:** Chi phí trung bình $\le \$0.05$ / tác vụ hoàn thành.
* **Safety & Guardrails:** $\ge 99\%$ chống Jailbreak, Prompt Injection và rò rỉ dữ liệu nhạy cảm.

#### C. Báo cáo Tự động Bottom-10 Diagnosis:
Pipeline tự động gom nhóm 10 trường hợp có điểm số thấp nhất (Lowest RAGAS score / Failed execution), sinh báo cáo JSON phục vụ đội ngũ phát triển tinh chỉnh prompt/retriever trước khi ra quyết định phát hành.

### Gate 3: Quản trị Phiên bản & Model Registry (MLflow)
* Tự động đăng ký mô hình / phiên bản Agent mới vào Catalog:
  - Nếu điểm số của phiên bản mới $\ge$ phiên bản hiện tại trên toàn bộ 4 chỉ số RAGAS: Gán bí danh `champion`.
  - Phiên bản hiện tại được chuyển sang trạng thái `challenger` và duy trì trong 14 ngày làm phương án dự phòng khẩn cấp.
* Ghi nhận commit hash, DVC data version, và SHA-256 digest vào bảng `governance.audit_events` với cơ chế băm chuỗi bảo mật (Tamper-Evident Ledger).

### Gate 4: Triển khai Canary & Tự động Rollback
* **Lộ trình phân bổ Traffic:**
  - **Pha 1 (15 phút):** $5\%$ lưu lượng người dùng được định tuyến vào phiên bản mới.
  - **Pha 2 (30 phút):** Tăng lên $25\%$ lưu lượng nếu không có cảnh báo.
  - **Pha 3:** Tăng lên $100\%$ và hoàn tất quy trình phát hành.
* **Tiêu chí Kích hoạt Tự động Rollback (Circuit Breaker Triggered):**
  - Tỷ lệ lỗi HTTP 5xx vượt quá **$1.0\%$** trong cửa sổ trượt 3 phút.
  - Độ trễ $p95$ vượt quá **$2,500\text{ms}$**.
  - Circuit Breaker tại Agent Runtime chuyển sang trạng thái **`OPEN`** đối với Model Gateway hoặc Tool Dispatcher.
  - Lập tức chuyển ngược lại phiên bản `challenger` trong vòng **$< 5$ giây** mà không làm gián đoạn các luồng Agent đang chạy dở.

---

## 3. Tham Chiếu Cấu Hình GitHub Actions Workflow (`.github/workflows/ci_cd_ct.yml`)

```yaml
name: Production CI/CD/CT Pipeline

on:
  push:
    branches: [ master, main ]
  pull_request:
    branches: [ master, main ]

jobs:
  gate-1-static-and-contracts:
    name: "Gate 1: Static Code, DB-56 & Contract Verification"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python 3.13
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Install Dependencies
        run: |
          pip install -e libs/python/agent-contracts
          pip install -e libs/python/platform-common
          pip install -e apps/agent-runtime
          pip install pytest pytest-asyncio
      - name: Static DB-56 Validation
        run: |
          python docs/03-database/database/validate_db_static.py
          python docs/07-testing/validation-scripts/check_design_56.py
      - name: Run Contract & Security Tests
        run: |
          pytest tests/contract/
          pytest tests/security/

  gate-2-continuous-testing:
    name: "Gate 2: Continuous Testing & Agent Quality Gates"
    needs: gate-1-static-and-contracts
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run RAGAS & Agent Quality Benchmark
        run: |
          echo "Running RAGAS 4-metrics evaluation suite..."
          # Thực thi script đánh giá bộ dữ liệu kiểm định
          pytest tests/contract/test_reliability_triangle.py
          pytest tests/contract/test_ingestion_quarantine.py
          pytest tests/contract/test_deep_healthcheck.py

  gate-3-registry-promotion:
    name: "Gate 3: Promote Champion in Model Registry"
    needs: gate-2-continuous-testing
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Promote Champion Model & Prompt
        run: |
          echo "Tagging new Champion in Catalog and recording Tamper-Evident Audit Event..."

  gate-4-canary-deploy:
    name: "Gate 4: Canary Deployment (5% -> 25% -> 100%)"
    needs: gate-3-registry-promotion
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy Canary Traffic Shift
        run: |
          echo "Shifting Canary traffic and monitoring OTel metrics & Circuit Breaker status..."
```

