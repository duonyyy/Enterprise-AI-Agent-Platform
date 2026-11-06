# 📁 Kho Tư Liệu Thiết Kế & Hệ Thống Tài Sản Đồ Họa (Design Assets & Brand System)

Thư mục này là **Kho lưu trữ tài nguyên thiết kế chính thức** của nền tảng **Enterprise AI Agent Platform**, lưu trữ trọn bộ tài sản nhận diện thương hiệu, bộ icon hệ thống 29 biểu tượng, logo vector/raster, favicon, bản chụp giao diện và tài liệu thiết kế.

> ℹ️ **Ghi chú quan trọng cho Đội ngũ Kỹ thuật & Thiết kế:**  
> - Định hướng thẩm mỹ chính thức: **Option A — Mineral & Copper** (Canvas `#FAFAF8`, Sidebar Graphite `#18252A`, Deep Teal `#0F766E`, Burnished Copper `#C2410C`).
> - Tài nguyên tĩnh phục vụ trực tiếp runtime cho ứng dụng Next.js được đồng bộ tại:  
>   🔗 **[`apps/studio-web/public/`](../apps/studio-web/public/)** (`/public/brand/`, `/public/icons/`, `/public/favicon.ico`)
> - Tệp registry quản lý tài sản typesafe trong code:  
>   🔗 **[`apps/studio-web/src/lib/brand-assets.ts`](../apps/studio-web/src/lib/brand-assets.ts)**
> - Cẩm nang thiết kế chi tiết:  
>   🔗 **[`docs/04-module-specs/studio/DESIGN_BRIEF_FOR_DESIGNER.md`](../docs/04-module-specs/studio/DESIGN_BRIEF_FOR_DESIGNER.md)**.

---

## 🗂️ Cấu Trúc Phân Tầng Thư Mục `assets/`

```text
assets/
├── README.md                                    # [BẠN ĐANG Ở ĐÂY] Bản đồ tài nguyên & mục lục
├── Sơ đồ Harness Agent Pastel Màu Nước.png      # Sơ đồ kiến trúc tổng quan nền tảng (dùng trong README gốc)
│
├── brand/                                       # Hệ thống Nhận diện Thương hiệu & Logo (Concept 03 Portal Monogram)
│   ├── README.txt                               # Đặc tả kỹ thuật & ghi chú từ designer
│   ├── svg/                                     # Logo vector SVG chuẩn mã sạch
│   │   ├── logo-horizontal-light.svg            # Logo ngang đầy đủ trên nền sáng
│   │   ├── logo-horizontal-dark.svg             # Logo ngang đầy đủ trên nền tối (Sidebar expanded)
│   │   ├── logo-symbol-color.svg                # Biểu tượng Portal Monogram màu chuẩn (#0F766E & #C2410C)
│   │   ├── logo-symbol-reference-tone.svg       # Biểu tượng tông màu tham chiếu
│   │   ├── logo-symbol-white.svg                # Biểu tượng đơn sắc trắng (cho nền tối đậm)
│   │   └── logo-symbol-black.svg                # Biểu tượng đơn sắc đen (in ấn, đóng dấu)
│   ├── png/                                     # Logo raster phân giải cao trong suốt
│   │   ├── logo-horizontal-light.png
│   │   ├── logo-horizontal-dark.png
│   │   ├── logo-symbol-color.png
│   │   ├── logo-symbol-reference-tone.png
│   │   ├── logo-symbol-white.png
│   │   └── logo-symbol-black.png
│   ├── favicons/                                # Bộ biểu tượng trình duyệt & ứng dụng di động
│   │   ├── favicon.ico                          # Multi-size ICO (16x16, 32x32, 48x48)
│   │   ├── favicon-16.png ... favicon-512.png   # Favicon web raster chuẩn các kích cỡ
│   │   ├── app-icon-192.png                     # Icon ứng dụng PWA 192px
│   │   └── app-icon-512.png                     # Icon ứng dụng PWA 512px
│   └── previews/                                # Bảng trình bày duyệt concept
│       └── concept-03-corrected-board.png       # Bảng duyệt Concept 03 Monogram chính thức
│
├── icons/                                       # Hệ thống Icon Đồ họa 29 Biểu Tượng Chuẩn Doanh Nghiệp
│   ├── README.md                                # Hướng dẫn kỹ thuật & tích hợp Next.js/React
│   ├── MANIFEST.json                            # Từ điển metadata mô tả 29 icon
│   ├── QA_REPORT.md                             # Báo cáo kiểm định chất lượng hiển thị
│   ├── QA_REPORT.csv                            # Bảng kiểm tra chi tiết từng pixel & padding
│   ├── svg/                                     # 29 tệp SVG vector 24x24 (stroke=currentColor, stroke-width=1.5)
│   │   ├── navigation/                          # 13 icon điều hướng Sidebar
│   │   │   ├── nav-agents.svg                   # Tác tử AI (Agents)
│   │   │   ├── nav-approvals.svg                # Hộp thư phê duyệt (Approval Inbox)
│   │   │   ├── nav-business.svg                 # Nghiệp vụ doanh nghiệp
│   │   │   ├── nav-evaluations.svg              # Đánh giá & kiểm định (Evaluations)
│   │   │   ├── nav-knowledge.svg                # Kho tri thức (Knowledge Base)
│   │   │   ├── nav-mcp.svg                      # Máy chủ MCP (MCP Servers)
│   │   │   ├── nav-overview.svg                 # Tổng quan (Overview)
│   │   │   ├── nav-runs.svg                     # Lịch sử chạy (Runs & Traces)
│   │   │   ├── nav-settings.svg                 # Cài đặt workspace (Settings)
│   │   │   ├── nav-skills.svg                   # Kỹ năng (Skills)
│   │   │   ├── nav-tools.svg                    # Đăng ký công cụ (Tool Registry)
│   │   │   ├── nav-workflows.svg                # Quy trình (Workflows)
│   │   │   └── nav-workspace.svg                # Bộ chọn workspace
│   │   └── status-actions/                      # 16 icon trạng thái vòng đời & thao tác
│   │       ├── action-approve.svg               # Phê duyệt
│   │       ├── action-compare.svg               # So sánh phiên bản
│   │       ├── action-publish.svg               # Công bố (Publish)
│   │       ├── action-reject.svg                # Từ chối phê duyệt
│   │       ├── action-rollback.svg              # Quay về bản trước
│   │       ├── run-cancelled.svg                # Lần chạy bị hủy
│   │       ├── run-failed.svg                   # Lần chạy thất bại
│   │       ├── run-running.svg                  # Đang chạy
│   │       ├── run-succeeded.svg                # Chạy thành công
│   │       ├── run-waiting-approval.svg         # Chờ con người phê duyệt
│   │       ├── safety-quarantine.svg            # Vùng cách ly an toàn
│   │       ├── safety-shield.svg                # Khiên bảo vệ an ninh
│   │       ├── status-archived.svg              # Đã lưu trữ
│   │       ├── status-draft.svg                 # Bản nháp
│   │       ├── status-immutable.svg             # Bất biến (Đã khóa)
│   │       └── status-published.svg             # Đã phát hành chính thức
│   ├── png/                                     # 145 tệp PNG nền trong suốt (màu Deep Teal #0F766E)
│   │   ├── 16px/                                # 29 icon tỉ lệ 16x16
│   │   ├── 24px/                                # 29 icon tỉ lệ 24x24
│   │   ├── 64px/                                # 29 icon tỉ lệ 64x64
│   │   ├── 128px/                               # 29 icon tỉ lệ 128x128
│   │   └── 512px/                               # 29 icon tỉ lệ 512x512
│   └── previews/                                # Ảnh chụp kiểm định hiển thị
│       ├── all-icons.png                        # Tổng mục lục 29 icon
│       ├── light-background.png                 # Hiển thị trên nền sáng Mineral Sand #FAFAF8
│       ├── dark-background.png                  # Hiển thị trên nền tối Mineral Graphite #18252A
│       └── 16px-test.png                        # Kiểm tra độ rõ nét ở 16px
│
├── concepts/                                    # Tư liệu phác thảo, moodboard & concept boards
│   ├── concept_exploration_chatgpt_01.png       # Khảo sát thẩm mỹ ban đầu
│   ├── concept_exploration_chatgpt_02.png       # Khảo sát phối màu và bố cục
│   └── So_do_Harness_Agent_Pastel_Mau_Nuoc.png  # Bản vẽ sơ đồ kiến trúc harness
│
├── screenshots/                                 # Ảnh chụp màn hình kiểm định các pha giao diện
│   ├── after_mineral_desktop.png                # Console trên desktop màn hình lớn
│   ├── after_mineral_laptop.png                 # Console trên màn hình laptop 13-14 inch
│   ├── after_mineral_tablet.png                 # Console trên tablet
│   ├── after_mineral_mobile.png                 # Danh sách thẻ xếp tầng trên điện thoại
│   └── after_mineral_modal.png                  # Hộp thoại tạo mới agent
│
└── archives/                                    # Gói tệp gốc nén (Bản lưu trữ bảo toàn dữ liệu)
    ├── enterprise-agent-platform-icons.zip      # Zip gốc bộ 29 icon
    └── enterprise-agent-platform-portal-monogram-corrected.zip # Zip gốc logo monogram concept 03
```

---

## 🎨 1. Hệ Thống Thương Hiệu Master (`brand/`)

| Tệp SVG | Tệp PNG | Ý Nghĩa / Mục Đích Sử Dụng | Vị Trí Triển Khai |
| :--- | :--- | :--- | :--- |
| `logo-horizontal-light.svg` | `logo-horizontal-light.png` | Logo đầy đủ gồm biểu tượng + chữ trên nền sáng | Trang Đăng nhập, Header tài liệu |
| `logo-horizontal-dark.svg` | `logo-horizontal-dark.png` | Logo đầy đủ gồm biểu tượng + chữ trên nền tối | **Thanh bên Sidebar mở rộng** (nền Graphite `#18252A`) |
| `logo-symbol-color.svg` | `logo-symbol-color.png` | Biểu tượng Portal Monogram màu chuẩn Deep Teal & Copper | **Thanh bên Sidebar thu gọn** (Collapsed 68px), Avatar |
| `logo-symbol-white.svg` | `logo-symbol-white.png` | Biểu tượng đơn sắc trắng | Nền ảnh tối màu, watermark |
| `logo-symbol-black.svg` | `logo-symbol-black.png` | Biểu tượng đơn sắc đen | Đóng dấu pháp lý, in ấn đen trắng |
| `concept-03-corrected-board.png` | — | Bảng trình bày Concept 03 Monogram chính thức | Tài liệu nghiệm thu thiết kế |

---

## 🧭 2. Hệ Thống 29 Icon Chuẩn Hóa (`icons/`)

- **Kích thước chuẩn:** `24 × 24 px`, `viewBox="0 0 24 24"`.
- **Nét vẽ:** `stroke-width="1.5"`, bo góc mềm đầu nét `stroke-linecap="round"` và `stroke-linejoin="round"`.
- **Tính kế thừa màu:** Thuộc tính `stroke="currentColor"` cho phép icon tự động đổi màu mượt mà theo CSS (Teal trên nền sáng, Trắng ngà trên nền tối Graphite).
- **Phân loại:**
  - **13 Icon Navigation (`svg/navigation/`):** Tương ứng 1:1 với các tuyến đường trên Sidebar của Studio.
  - **16 Icon Status & Actions (`svg/status-actions/`):** Tương ứng với vòng đời `draft | published | immutable`, trạng thái thực thi `running | waiting | succeeded | failed` và các hành động phê duyệt.

---

## 🚀 3. Đồng Bộ Trực Tiếp Vào Ứng Dụng Web (`apps/studio-web`)

Tất cả các tài sản đồ họa cần thiết đã được đồng bộ sang `apps/studio-web/public/` để ứng dụng web sẵn sàng chạy ngay lập tức:

```text
apps/studio-web/public/
├── favicon.ico                          # Multi-size favicon cho trình duyệt
├── brand/                               # Toàn bộ logo SVG, PNG và favicon bộ
└── icons/
    ├── navigation/                      # 13 icon SVG sidebar
    └── status-actions/                  # 16 icon SVG trạng thái
```

Sử dụng trong mã nguồn Next.js thông qua registry [`apps/studio-web/src/lib/brand-assets.ts`](../apps/studio-web/src/lib/brand-assets.ts):

```typescript
import { BRAND_LOGOS, NAV_ICONS, STATUS_ACTION_ICONS } from '@/lib/brand-assets';

// Ví dụ: Hiển thị Logo Sidebar
<img src={BRAND_LOGOS.horizontalDarkSvg} alt="Agent Studio" height={28} />

// Ví dụ: Hiển thị Icon Navigation
<img src={NAV_ICONS.agents} alt="AI Agents" width={20} height={20} />
```
