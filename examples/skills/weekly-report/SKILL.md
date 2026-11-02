---
name: weekly-report
description: Tạo báo cáo tiến độ tuần có cấu trúc, dựa trên dữ liệu công việc được cung cấp. Dùng khi người dùng yêu cầu tổng hợp báo cáo tuần, nêu blockers hoặc kết quả hoàn thành.
license: Proprietary
metadata:
  author: enterprise-platform
  version: "1.0"
---

# Weekly Report Skill

## Khi nào dùng
- Người dùng cần báo cáo tiến độ tuần dựa trên dữ liệu có nguồn kiểm chứng.
- Cần tổng kết các hạng mục công việc hoàn thành và các trở ngại (blockers).

## Quy trình xử lý
1. Kiểm tra dữ liệu đầu vào và hỏi lại nếu thiếu thông tin quan trọng.
2. Sắp xếp công việc theo trạng thái và mức độ ưu tiên.
3. Đọc `references/report-template.md` khi cần mẫu trình bày báo cáo tiêu chuẩn.
4. Tạo bản nháp và nêu rõ các số liệu chưa được xác minh.

## Ranh giới an toàn
- Không suy diễn dữ liệu không có trong nguồn.
- Không gửi email hoặc gọi tool thay đổi hệ thống bên ngoài khi chưa có Tool grant và approval từ người có thẩm quyền.

