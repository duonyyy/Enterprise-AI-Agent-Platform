# ADR-009-IMMUTABLE-SNAPSHOTS: Immutable Publishing & Checksums

**Trạng thái:** APPROVED  
**Ngày quyết định:** 2026-10-30  
**Tác động:** Toàn hệ thống Enterprise AI Agent Platform  

## 1. Bối cảnh
Hệ thống cần đảm bảo tính nhất quán, bảo mật đa tenant và khả năng mở rộng ở mức doanh nghiệp.

## 2. Quyết định Kiến trúc
Các phiên bản phát hành Agent, Skill, Tool, Workflow là bất biến, gắn kèm bundle_sha256.

## 3. Hệ quả
- **Tích cực:** Kiến trúc đồng nhất, bảo vệ tài nguyên tuyệt đối, dễ bảo trì và mở rộng.
- **Ràng buộc:** Cần tuân thủ nghiêm ngặt các quy tắc kiểm tra tại API Gateway và Tool Gateway.
