# Nguồn chính thức — api-contract-testing

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## OpenAPI Specification latest
- URL: https://spec.openapis.org/oas/latest.html
- Dùng để: Đặc tả giao diện HTTP; khóa version theo tài liệu của dự án.

## JSON Schema Draft 2020-12
- URL: https://json-schema.org/draft/2020-12
- Dùng để: Validation dialect và nullability; đừng áp dụng sai cho OAS3.0.

## Pact documentation
- URL: https://docs.pact.io/
- Dùng để: Consumer-driven contract, provider verification.

## Pact Broker Can I Deploy
- URL: https://docs.pact.io/pact_broker/can_i_deploy
- Dùng để: Đánh giá version compatibility trước deploy.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
