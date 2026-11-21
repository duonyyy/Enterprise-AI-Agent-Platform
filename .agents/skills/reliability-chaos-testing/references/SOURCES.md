# Nguồn chính thức — reliability-chaos-testing

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## Principles of Chaos Engineering
- URL: https://principlesofchaos.org/
- Dùng để: Steady-state hypothesis, real-world faults, minimize blast radius.

## Google SRE Workbook
- URL: https://sre.google/workbook/
- Dùng để: SLI/SLO, incident response và reliability practice.

## Chaos Mesh Documentation
- URL: https://chaos-mesh.org/docs/
- Dùng để: Ví dụ fault injection trên Kubernetes; chỉ dùng khi đúng môi trường.

## OpenTelemetry Documentation
- URL: https://opentelemetry.io/docs/
- Dùng để: Tracing/metrics liên kết sự cố với hệ quả.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
