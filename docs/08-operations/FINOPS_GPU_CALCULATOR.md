# LLM FINOPS & GPU SERVING ARCHITECTURE GUIDE
**Enterprise AI Agent Platform — Production FinOps & High-Throughput Serving (VinAI Capstone Track 2 Day 20 & Day 25)**

---

## 1. Mô Hình Tính Toán Roofline Model Trong LLM Serving

Kiến trúc Transformer trong LLM có đặc thù vận hành qua 2 pha hoàn toàn tách biệt về mặt tài nguyên phần cứng:

```
                            ROOFLINE MODEL CHO LLM
       Peak Compute (FLOPs/s) ──────────────────────┐ (Prefill Phase)
                             /                      │ Compute Bound
                            /                       │
                           /                        │
                          /                         │
     Memory Bandwidth    /                          │
        (Bytes/s)       /                           │ (Decode Phase)
                       /                            │ Memory Bound
                      └─────────────────────────────┴──────────────
                      0                           I* (Arithmetic Intensity)
```

### 1.1. Pha 1: Prefill Phase (Xử lý Prompt đầu vào)
* **Đặc tính:** **Compute Bound** (Giới hạn bởi năng lực tính toán).
* **Cơ chế:** Toàn bộ $N$ token đầu vào của prompt được đưa vào GPU cùng lúc. Các phép nhân ma trận có kích thước lớn ($[B \times N, D] \times [D, D]$), cho phép đạt cường độ số học (Arithmetic Intensity) cao.
* **Tối ưu:** vLLM sử dụng Chunked Prefill để chia nhỏ prompt dài thành các chunk (ví dụ 512 tokens), xen kẽ với bước Decode để tránh làm nghẽn GPU và giảm TTFT (Time To First Token).

### 1.2. Pha 2: Decode Phase (Sinh Token tự hồi quy)
* **Đặc tính:** **Memory Bandwidth Bound** (Giới hạn bởi băng thông bộ nhớ VRAM).
* **Cơ chế:** Mỗi bước sinh token, GPU chỉ xử lý duy nhất 1 token mới cho mỗi chuỗi trong batch. Để tính 1 token, GPU phải đọc toàn bộ hàng chục tỷ trọng số mô hình (weights) từ VRAM vào SRAM của chip.
* **Tối ưu:** Tăng Batch Size và sử dụng PagedAttention để nạp weights một lần nhưng tính cho nhiều request đồng thời, đẩy MBU (Memory Bandwidth Utilization) lên tối đa.

---

## 2. Các Chỉ Số Hiệu Suất Phần Cứng Cốt Lõi (MFU & MBU)

### 2.1. Model FLOPs Utilization (MFU)
Đo lường tỷ lệ phần trăm giữa năng lực tính toán thực tế đạt được so với đỉnh lý thuyết của GPU:

$$\text{MFU} = \frac{\text{Observed FLOP/s}}{\text{Theoretical Peak FLOP/s}}$$

* **Công thức ước lượng FLOPs:** Đối với mô hình kích thước $P$ tham số, mỗi token cần khoảng $2P$ FLOPs trong pha sinh:
  $$\text{Observed FLOP/s} = \frac{2 \times P \times \text{Tokens Generated}}{\text{Time (seconds)}}$$
* **Ngưỡng mục tiêu doanh nghiệp:**
  - Serving thông thường (Naive HuggingFace): $\text{MFU} \approx 10\% - 18\%$ (Lãng phí phần cứng nặng).
  - vLLM / TensorRT-LLM tối ưu: $\text{MFU} \ge 45\% - 55\%$.

### 2.2. Memory Bandwidth Utilization (MBU)
Đo lường tỷ lệ sử dụng băng thông bộ nhớ thực tế trong pha Decode:

$$\text{MBU} = \frac{\text{Model Weights Size (Bytes)} \times \text{Tokens per Second}}{\text{Peak GPU Memory Bandwidth (Bytes/s)}}$$

* **Ví dụ:** Llama-3-70B ở định dạng FP16 chiếm $\approx 140\text{ GB}$. Trên 2 card NVIDIA H100 (Băng thông $3.35\text{ TB/s} \times 2 = 6.7\text{ TB/s}$), nếu đạt $30\text{ tokens/s}$:
  $$\text{MBU} = \frac{140 \times 10^9 \times 30}{6.7 \times 10^{12}} \approx 62.6\% \quad (\text{Mục tiêu: } \ge 65\% - 75\%)$$

---

## 3. Quản Lý Bộ Nhớ KV Cache: PagedAttention & Continuous Batching

### 3.1. Công thức bộ nhớ KV Cache
Bộ nhớ cần thiết để lưu trữ Key-Value Cache tăng tuyến tính theo độ dài chuỗi và batch size:

$$\text{Memory}_{\text{KV}} = 2 \times n_{\text{layers}} \times n_{\text{heads\_kv}} \times d_{\text{head}} \times \text{seq\_len} \times \text{batch\_size} \times \text{bytes\_per\_element}$$

* **Ví dụ thực tế với Llama-3-70B** ($n_{\text{layers}}=80$, $n_{\text{heads\_kv}}=8$ GQA, $d_{\text{head}}=128$, FP16 = 2 bytes):
  - Mỗi token tiêu tốn: $2 \times 80 \times 8 \times 128 \times 2 = 327,680\text{ Bytes} \approx 320\text{ KB/token}$.
  - Với ngữ cảnh $8,192$ tokens cho 1 request: tiêu tốn $\approx 2.56\text{ GB}$ VRAM chỉ riêng cho KV Cache!

### 3.2. PagedAttention vs Phân mảnh bộ nhớ truyền thống
* **Vấn đề của Attention truyền thống:** Cần cấp phát bộ nhớ liên tục (contiguous memory) cho chiều dài tối đa (`max_seq_len`). Dẫn đến hiện tượng phân mảnh nội bộ (Internal Fragmentation) và phân mảnh bên ngoài (External Fragmentation) làm **lãng phí 60% - 80% dung lượng VRAM**.
* **Giải pháp PagedAttention:** Lấy cảm hứng từ Virtual Memory / Paging của Hệ điều hành. KV Cache được chia thành các khối cố định (blocks, ví dụ 16 tokens/block).
  - Giảm tỷ lệ lãng phí bộ nhớ xuống **$< 4\%$**.
  - Cho phép tăng kích thước Batch Size lên $2.5\times - 4\times$ trên cùng một phần cứng GPU.

### 3.3. Continuous Batching (Iteration-Level Scheduling)
* Giải quyết triệt để vấn đề **Bubble Time**: Khi batch gồm nhiều request có độ dài câu trả lời khác nhau, hệ thống truyền thống phải đợi request dài nhất sinh xong mới nạp batch mới.
* Continuous batching cho phép request hoàn thành thoát ra ngay lập tức và nạp ngay request mới vào slot trống ở cấp độ từng bước giải mã (iteration-level).

---

## 4. Công Thức Tính Toán Chi Phí Đơn Vị (Unit Economics Formula)

Công thức xác định chi phí vận hành cho mỗi 1 triệu tokens ($\$/1\text{M-tokens}$) để báo cáo bài toán kinh tế cấp Enterprise:

$$\text{Cost per 1M Tokens} = \frac{\text{GPU Cost per Hour (\$/hr)} \times 1,000,000}{\text{Tokens Generated per Hour}}$$

Trong đó:
$$\text{Tokens Generated per Hour} = \text{Average Throughput (Tokens/s)} \times 3600 \times \text{Effective Utilization Factor}$$

### Bảng Tính So Sánh Chi Phí Thực Tế Giữa Các Dòng GPU (Ước lượng vLLM)

| Dòng GPU | Cấu hình VRAM & Băng thông | Giá thuê trung bình (\$/hr) | Model phục vụ | Throughput trung bình | Chi phí / 1M Tokens | So sánh với OpenAI GPT-4o (\$5.00/1M) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **NVIDIA L4** | 24GB GDDR6 (300 GB/s) | \$0.75 / hr | Llama-3-8B (FP16) | 120 tokens/s | **\$1.73 / 1M tokens** | Tiết kiệm **65%** |
| **NVIDIA A100** | 80GB SXM4 (2.0 TB/s) | \$2.20 / hr | Llama-3-8B (Batch 32) | 850 tokens/s | **\$0.72 / 1M tokens** | Tiết kiệm **85%** |
| **2x H100** | 160GB HBM3 (6.7 TB/s) | \$7.00 / hr (2 card) | Llama-3-70B (AWQ 4-bit) | 1,400 tokens/s | **\$1.38 / 1M tokens** | Tiết kiệm **72%** |

---

## 5. Chiến Lược Xếp Tầng Giảm Giá (FinOps Discount Stacking)

Để đạt mức chi phí tối ưu nhất cho nền tảng doanh nghiệp, hệ thống áp dụng chiến lược **4 tầng xếp chồng giảm giá (Discount Stacking)**:

```
                            CHIẾN LƯỢC DISCOUNT STACKING
   ┌───────────────────────────────────────────────────────────────┐
   │ 1. Semantic Vector Cache (Cosine >= 0.90)                     │ -> Giảm 20-30% tổng LLM calls
   │    Bypass GPU hoàn toàn, trả lời trong < 5ms với chi phí $0    │    (Tiết kiệm 100% cho cache hit)
   ├───────────────────────────────────────────────────────────────┤
   │ 2. Prefix / Prompt Caching (KV Cache reuse)                   │ -> Giảm 75-80% chi phí input tokens
   │    Tái sử dụng KV cache của System Prompt và Skill context    │    cho các Agent chạy lặp lại
   ├───────────────────────────────────────────────────────────────┤
   │ 3. Spot GPU Instances cho Batch Ingestion & Offline Eval      │ -> Tiết kiệm 60-70% giá thuê compute
   │    Sử dụng Spot cho RAG Chunker và Eval Benchmark qua đêm     │    cho tải không nhạy cảm thời gian
   ├───────────────────────────────────────────────────────────────┤
   │ 4. 1-Year Committed Use Discounts (CUDs) cho Core Serving     │ -> Tiết kiệm 35-45% cho cụm GPU chính
   │    Cam kết tài nguyên cho cụm vLLM chạy 24/7                  │    phục vụ yêu cầu thời gian thực
   └───────────────────────────────────────────────────────────────┘
```

* **Kết quả tổng hợp:** Khi kết hợp cả 4 tầng, chi phí vận hành Agent tổng thể của doanh nghiệp giảm từ **55% đến 75%** so với việc gọi trực tiếp API thương mại bên ngoài.

