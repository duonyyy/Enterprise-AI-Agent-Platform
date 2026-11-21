# Nguồn chính thức — performance-load-testing

Tài liệu tham khảo độc lập, ưu tiên nguồn chính thức. Cần đối chiếu phiên bản với dependency/code của dự án; nguồn không tự động biến một test thành bằng chứng đã chạy.

## Grafana k6 docs
- URL: https://grafana.com/docs/k6/latest/
- Dùng để: Công cụ thực thi kiểm thử tải.

## k6 Scenarios
- URL: https://grafana.com/docs/k6/latest/using-k6/scenarios/
- Dùng để: Thiết kế workloads và scheduling.

## k6 Thresholds
- URL: https://grafana.com/docs/k6/latest/using-k6/thresholds/
- Dùng để: Pass/fail metrics; check khác threshold.

## k6 Arrival Rate
- URL: https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/
- Dùng để: Open workload model và iteration rate.

## Nguyên tắc sử dụng

- Dùng tiêu chuẩn làm căn cứ thiết kế và review; mapping theo control/criterion cụ thể khi có thể.
- Không sao chép toàn bộ nội dung tiêu chuẩn; tập trung vào yêu cầu và bằng chứng tại dự án.
- Nếu tài liệu/version khác thực tế repo, lấy source-of-truth dự án được phê duyệt, ghi rõ khác biệt.
