# ADR-013-GREENFIELD-BASELINE-56-TABLES: Greenfield Unified Baseline V1.0 (56 Tables)

**Trạng thái:** APPROVED  
**Ngày quyết định:** 2026-10-30  
**Tác động:** Toàn hệ thống Enterprise AI Agent Platform  

## 1. Bối cảnh
Hệ thống cần đảm bảo tính nhất quán, bảo mật đa tenant và khả năng mở rộng ở mức doanh nghiệp.

## 2. Quyết định Kiến trúc
Hợp nhất 56 bảng / 7 schemas, 55 chính sách Force RLS, 10 immutability triggers vào 0001_baseline.sql ngay từ Day-1.

## 3. Hệ quả
- **Tích cực:** Kiến trúc đồng nhất, bảo vệ tài nguyên tuyệt đối, dễ bảo trì và mở rộng.
- **Ràng buộc:** Cần tuân thủ nghiêm ngặt các quy tắc kiểm tra tại API Gateway và Tool Gateway.
