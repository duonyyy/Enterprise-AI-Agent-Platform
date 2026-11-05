# Kiểm tra khi nạp DB56 fixtures

Đây là kế hoạch, không phải kết quả PostgreSQL đã pass. Dùng database riêng đã được cho phép và manifest đúng profile. Giữ application/worker/dispatcher đang dùng tách khỏi database fixture.

## Migration và role

Kit gốc chỉ mô tả baseline `0001` và cảnh báo missing workspace có thể mở rộng phạm vi. Dự án hiện có `0002_fail_closed_rls.sql` yêu cầu đủ tenant/workspace trên bảng scoped. Không dùng cảnh báo lịch sử đó như kết luận RLS hiện đang lỗi hoặc đã sửa đạt; phải kiểm bằng role thực trên migration chain hiện hành.

Trên kết nối test, xác minh database/user và role:

```sql
SELECT current_database(), current_user;
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
```

Test application isolation cần role không superuser, không BYPASSRLS và không được miễn RLS do ownership. Kiểm policy và FORCE RLS thực tế. Role seed/migration có quyền INSERT không tự đại diện role ứng dụng. Không cấp thêm quyền trong bài kiểm tra chỉ để nhận PASS.

## Ma trận đọc

Lấy UUID từ manifest; không dùng placeholder dưới đây như UUID thực. Trên cùng connection role app, mỗi ca dùng transaction mới:

```sql
BEGIN;
SELECT set_config('app.tenant_id', '<tenant-A-uuid>', true);
SELECT set_config('app.workspace_id', '<workspace-A-uuid>', true);
SELECT count(*) FROM catalog.agents;
SELECT count(*) FROM catalog.agents WHERE tenant_id <> current_setting('app.tenant_id')::uuid;
SELECT count(*) FROM catalog.agents WHERE workspace_id <> current_setting('app.workspace_id')::uuid;
ROLLBACK;
```

Đối chiếu count hợp lệ với số Agent của workspace trong manifest. Hai count trái scope phải bằng 0. Thử tiếp `runtime.runs` và tài nguyên theo policy; các bảng global như `iam.users` có ownership/policy riêng, không suy diễn từ `catalog.agents`.

Các ca bắt buộc: tenant A/workspace A; tenant B/workspace B; cùng tenant khác workspace; thiếu tenant; thiếu workspace; thiếu cả hai; empty context và connection được trả về pool rồi chuyển scope. Dùng `demo` hoặc `load` cho ca cùng tenant nhiều workspace (`small` chỉ một workspace mỗi tenant). Thiếu context không được lộ scoped rows sau `0002`; query lỗi fail-closed và query trả rỗng cần ghi đúng hành vi quan sát, không giả kết quả.

Counts tổng của `verify_table_counts.sql` phụ thuộc quyền. Counts đúng không thay negative authorization test. GUC context không phải cơ chế xác thực người dùng; direct API test cần principal thật và RBAC/membership hiện hành.

## Các gate khác còn phải kiểm riêng

- PostgreSQL FK/CHECK/UNIQUE/triggers, replay cùng profile và transaction rollback khi import lỗi.
- API/UI đọc dữ liệu qua backend thực, quyền truy cập đúng; không sửa component để hiển thị trực tiếp JSON mẫu thay API.
- Không publish/invoke/resume/dispatch các fixture Agent/Tool/Skill/MCP/approval/jobs/outbox. Nhãn JSON không phải enforcement; không mở các thao tác này trên database chứa fixture.
- Nguồn `seed://`/vector pending/checkpoint mẫu phải không được quảng cáo như nguồn retrieval hoặc recovery hợp lệ.
- Không dùng số usage/evaluation/audit tổng hợp làm kết quả vận hành hoặc benchmark.
- Negative writes chỉ chạy trên target được phép với fixture và rollback phù hợp; không thêm test thay đổi dữ liệu thật.

Ghi command, role, migration fingerprints, profile, expected/actual và evidence `PASS/FAIL/NOT_RUN/BLOCKED`. Không đưa password/token vào artifact. Không drop/reset database hay xóa volume ngoài chỉ dẫn riêng.
