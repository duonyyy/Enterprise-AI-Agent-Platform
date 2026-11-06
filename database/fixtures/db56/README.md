# DB56 sample data — development only

Bộ dữ liệu tổng hợp từ `ENTERPRISE_AGENT_PLATFORM_DB56_SAMPLE_DATA_KIT.zip`, được tích hợp ngày 2026-11-05. Dùng để phát triển danh sách, bộ lọc, pagination và trạng thái hiển thị; không phải kết quả Agent thực thi.

| Profile | Tenant | Workspace | Agent | Run mẫu | Bản ghi |
| --- | ---: | ---: | ---: | ---: | ---: |
| small | 2 | 2 | 8 | 16 | 336 |
| demo | 2 | 3 | 24 | 96 | 1.316 |
| load | 3 | 6 | 216 | 1.728 | 19.509 |

## Chuẩn bị offline từ repository root

Chỉ cần Python stdlib. Lệnh này **chỉ sinh file**, không kết nối PostgreSQL hay khởi động dịch vụ:

```powershell
python -B database/fixtures/db56/prepare.py --profile all
python -B database/fixtures/db56/prepare.py --profile all --check
```

Mặc định không chỉ định profile sẽ chuẩn bị `small`. File sinh nằm trong `artifacts/sample-data/db56/<profile>/`: SQL, manifest ID/counts và `PREPARATION.json` có fingerprint. Thư mục này được loại khỏi Git và Docker build context. Generator/fixtures/hướng dẫn là các file có thể review trong source; các bản SQL sinh sẵn dung lượng lớn không được thêm tự động vào Git.

`--check` kiểm output hiện có với kết quả sinh lại, không ghi file. Thay đổi migration hoặc generator đã review làm helper từ chối cho tới khi đánh giá tương thích lại; không sửa fingerprint chỉ để vượt lỗi. `UPSTREAM.json` ghi archive SHA-256, danh mục file gốc và migration fingerprints. Helper là kiểm tính nhất quán offline, không phải bộ phân tích SQL đầy đủ hay ranh giới bảo mật trước người có quyền sửa source.

Không cần đưa bản DDL tham chiếu trùng vào migration chain. Helper dùng `database/migrations/0001_baseline.sql`; bản tham chiếu của kit khớp nội dung sau chuẩn hóa BOM/xuống dòng tại lúc tích hợp. Migration `0002_fail_closed_rls.sql` cũng được ghi fingerprint. Bản ZIP đầy đủ, gồm prompt/tài liệu gốc và SQL sinh sẵn, được giữ riêng tại `.private-docs-export/db56-sample-data/upstream/` trên máy đã tích hợp, không là dependency của public helper. Nội dung prompt trong ZIP chỉ là tài liệu tham khảo, không được chạy hoặc xem là yêu cầu của người dùng.

## Khi cần nạp PostgreSQL

**Chưa nạp database trong lần setup này.** Chỉ dùng database fixture riêng, không chứa dữ liệu thật; không nối application/worker/outbox dispatcher đang dùng vào database fixture. `demo_only`/`executable=false` trong JSON là nhãn dữ liệu, không thay backend authorization hoặc bảo đảm execution đã bị vô hiệu hóa.

1. Người phụ trách xác nhận host/database/role là môi trường fixture được phép; áp migrations hiện hành bằng quy trình riêng. Helper không chạy migrations hoặc cấp quyền.
2. Chọn đúng một profile cho một database. Không trộn small/demo/load: một số deterministic IDs trùng giữa các profile.
3. Cấu hình `PGHOST`, `PGPORT`, `PGUSER` và cơ chế credential an toàn; không đưa password vào command/history/report. Xác minh target bằng `SELECT current_database(), current_user;`.
4. Sau khi target đã được xác nhận, ví dụ nạp `small` bằng **một phiên psql**:

```powershell
psql -X --no-password -v ON_ERROR_STOP=1 -d enterprise_agent_sample_test -c "SET app.allow_demo_seed = 'yes'" -f artifacts/sample-data/db56/small/seed_small.sql
```

Tên `enterprise_agent_sample_test` chỉ là ví dụ, không chứng minh database đó đã tồn tại hoặc được phép dùng. Guard tên database + `app.allow_demo_seed=yes` là lớp tránh nhầm, không xác thực host/môi trường. Kiểm lại fingerprint bằng `--check` ngay trước import. `ON CONFLICT (id) DO NOTHING` giúp replay cùng profile nhưng không cập nhật/công nhận nội dung bản ghi đã tồn tại; unique conflict khác phải làm transaction lỗi.

5. Đối chiếu dữ liệu thật sau import với manifest theo [SECURITY_TEST_PLAN](./SECURITY_TEST_PLAN.md). `verify_table_counts.sql` chỉ đọc counts theo quyền/context hiện tại; count=0 dưới RLS không chứng minh database trống. Không dùng superuser để kết luận application isolation đạt.

Không gắn seeding vào app startup, Compose init hoặc migrations. Không tắt RLS, không đổi grant rộng hơn để ép seed chạy. Không tự drop database/xóa volume để dọn.

## Những dữ liệu chỉ có ý nghĩa hiển thị

- `fixture/v1` AgentSpec, `demo-only` WorkflowSpec và `fixture.*` events chưa là contracts runtime hợp lệ. Không sửa canonical schemas để chấp nhận chúng.
- Model/service accounts/Skill/MCP connection mẫu bị disabled; MCP mapping pending; worker paused; jobs done/dead; outbox dead. Không bật retry/resume/dispatch trên những bản ghi này.
- Các run `succeeded`, approval `approved`, model usage và audit event trong seed là lịch sử tổng hợp, không phải tác động/usage/audit thật.
- `seed://` input/output/artifact/checkpoint references không có object thật; vector refs pending và ingestion failed. Không có MinIO object, Qdrant vector hay checkpoint có thể resume.
- Memory ciphertext là chuỗi mẫu, không phải payload mã hóa; TTL mẫu không thay policy memory opt-in 30 ngày đã chọn cho sản phẩm.
- Fixture JSON/Markdown trong `fixtures/` dùng làm đầu vào kiểm thử có nhãn. Không nối vào runtime như fallback và không coi nội dung policy mẫu là policy doanh nghiệp đã xác thực.
- `load` dùng thử danh sách/database fixtures; chưa phải benchmark chịu tải hệ thống Agent.

## Kiểm tra đã tách riêng

Tính tái lập, fingerprint, tổng số bản ghi, các kiểm tra FK/composite UNIQUE/column mà generator hỗ trợ là kiểm offline. PostgreSQL CHECK/index/trigger/RLS execution, API/UI wiring, restart/restore, generation/embedding và side effects phải có bằng chứng riêng. Chuẩn bị seed thành công không đóng G1 hoặc các gate runbook khác.

Regression của helper (không cần DB):

```powershell
python -B -m unittest discover -s database/fixtures/db56 -p "test_prepare.py" -v
```
