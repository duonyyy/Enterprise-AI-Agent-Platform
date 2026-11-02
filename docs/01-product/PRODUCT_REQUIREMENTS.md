# PRODUCT REQUIREMENTS DOCUMENT (PRD) — ENTERPRISE AI AGENT PLATFORM

## 1. Tầm nhìn Sản phẩm
Enterprise AI Agent Platform là nền tảng quản trị và vận hành tác tử AI (AI Agents & Digital Workers) cấp doanh nghiệp, độc lập, học tập từ các chuẩn mực công nghiệp cao cấp (FPT AI Agents, FPT AI EOS, Model Context Protocol, Agent Skills).

Hệ thống cho phép các tổ chức:
1. Thiết kế, cấu hình Persona và phát hành các phiên bản Agent bất biến có bảo đảm tính toàn vẹn.
2. Đóng gói tri thức nghiệp vụ chuyên sâu thành các **Skills** có khả năng tái sử dụng cao, tiết kiệm chi phí suy luận (Token Containment).
3. Kết nối an toàn với hệ sinh thái công cụ doanh nghiệp thông qua **Model Context Protocol (MCP)** với lá chắn phòng vệ SSRF.
4. Điều phối các quy trình dài hạn (Long-running Workflows) với khả năng can thiệp của con người (**Human-in-the-Loop**) và khôi phục sau sự cố.

## 2. Chức năng Cốt lõi (Core Epics)
- **EPIC-01: Agent Studio & Lifecycle Management:** Soạn thảo Persona, liên kết Tools/Skills, kiểm soát phiên bản optimistic locking, đóng băng bất biến (bundle_sha256).
- **EPIC-02: Skill Management Engine:** Nạp tài liệu hướng dẫn `SKILL.md` theo cơ chế On-Demand Loading, quản lý phiên bản skill, sandbox chống Zip-Slip.
- **EPIC-03: Tool Gateway & MCP Integration:** Kết nối remote MCP servers qua Streamable HTTP, kiểm soát SSRF 4 lớp, thực thi tuần tự 12-Step ToolDispatcher, bảo đảm idempotency qua `(workspace_id, operation_key)`.
- **EPIC-04: Stateful Workflows & Approvals:** Đồ thị LangGraph lưu checkpoint vào CSDL, ngắt tiến trình chờ phê duyệt (`interrupt`), khôi phục chính xác sau restart.
- **EPIC-05: Enterprise Knowledge (RAG):** Quản lý kho tài liệu, dataset versioning, trích xuất chunk, vector search với phân quyền truy cập ACL.
- **EPIC-06: Multi-Tenant Governance & Security:** Cách ly dữ liệu khách hàng bằng PostgreSQL 16 Force RLS, 10 immutability triggers, nhật ký WORM audit trails.
