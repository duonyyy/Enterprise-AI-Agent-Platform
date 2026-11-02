# Security policy

- Không đưa mật khẩu, token, private key, OAuth credential hoặc dữ liệu tenant vào Git, issue hay log.
- Secret runtime được truyền qua secret manager hoặc `.env` local đã bị Git ignore; cấu hình production dùng role `platform_app` không có `BYPASSRLS`.
- Báo cáo lỗ hổng riêng tư cho maintainer dự án, kèm bước tái hiện tối thiểu và không công bố exploit/secret trong issue công khai.
- Không tự deploy production, rotate secret, cấp quyền hay chạy destructive migration. Những việc này cần người có thẩm quyền phê duyệt.
