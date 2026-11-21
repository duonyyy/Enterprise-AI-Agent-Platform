# Nguồn chính thức — security-testing

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## OWASP ASVS 5.0.0
- URL: https://asvs.owasp.org/
- Dùng để: Tập yêu cầu kiểm soát và xác minh; mapping theo các control phù hợp, không tự tuyên bố compliance.

## OWASP WSTG 4.2 stable
- URL: https://wstg.owasp.org/stable/4-Web_Application_Security_Testing/
- Dùng để: Phương pháp test auth, session, access control, input validation.

## OWASP API Security Top 10
- URL: https://owasp.org/API-Security/
- Dùng để: Rủi ro object/function-level authorization, security misconfiguration, API inventory.

## OWASP Session Management Cheat Sheet
- URL: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- Dùng để: Session token/cookie protection.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
