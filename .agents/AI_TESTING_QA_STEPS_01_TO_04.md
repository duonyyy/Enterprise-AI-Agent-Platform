# Sổ tay săn lỗi và kiểm thử phản biện — Bước 01–04

Document ID: QA-PLAYBOOK-0104 · Revision 2.0 · Cập nhật: 2026-11-17.

**Tài liệu thiết kế kiểm thử, không phải báo cáo nghiệm thu.** Mọi ca trong các ma trận dưới đây mặc định NOT_RUN. Biên tập tài liệu không có nghĩa đã chạy PostgreSQL, Redis, browser hoặc đã đóng G1–G4.

Mốc đối chiếu: HEAD `8d5619edbeebfbbc1a24d7b1978cedc1d89e0a93`, nhánh `feat/step-04c-agent-publish` cộng working tree chưa commit. Đây là snapshot, không phải yêu cầu quay về commit đó. Agent phải kiểm lại baseline và source hiện tại.

Tìm lỗi bằng cách kiểm tra bất biến và phản biện kết quả. Mỗi nhóm negative tests cần một positive control: hệ thống từ chối mọi request vẫn có thể qua nhiều test bảo mật nhưng không sử dụng được.

## 1. Nguồn quyết định kết quả đúng

Đọc [AGENTS](../AGENTS.md), [CONTRIBUTING](../CONTRIBUTING.md), [coding rules](../AGENT_CODING_RULES.md), [architecture](../ARCHITECTURE.md) và approved task packet được maintainer cung cấp.

Mỗi ca phải đối chiếu:

1. **Approved requirement:** actor, quyền, lifecycle và quyết định đã duyệt. Tài liệu draft có mặt trên máy không tự tạo authority.
2. **Canonical contract/schema:** raw request, response/error, constraints, version và compatibility.
3. **Source và thực thi:** route → auth → service → repository → storage/consumer; chứng minh requirement có được enforce không.

Nếu các nguồn mâu thuẫn, ghi NEEDS_DECISION, nêu hai cách hiểu và chặn đúng ca phụ thuộc. Không đổi expected theo code đang lỗi hoặc sửa quyền/schema để test xanh. Playbook không phê duyệt DDL, manifest, model capability hoặc environment authority.

Nguồn chính:

- [Control OpenAPI](../contracts/openapi/control-api.openapi.yaml), [AgentSpec v1](../contracts/jsonschema/agent-spec.v1.schema.json), [migrations](../database/migrations/), [migration runner](../scripts/run_migrations.py).
- Private bundle: task packets dưới docs/09-implementation/task-packets/, accepted ADRs dưới docs/02-architecture/adr/, SPEC và docs/07-testing/agent-lifecycle/ACCEPTANCE_PLAN.md. Public clone có thể không chứa docs/.
- [Review cuối Bước 04](../docs/09-implementation/reviews/20261117-step-04-final-review/REPORT.md) và [evidence manifest](../docs/09-implementation/reviews/20261117-step-04-final-review/VERIFICATION.json): snapshot lịch sử, không tự là kết quả baseline mới.

Không dùng README, tên test, tổng test xanh hoặc chữ COMPLETE trong báo cáo cũ thay bằng chứng thực.

## 2. Chọn skill và giới hạn tác động

[9 skill QA](./AI_TESTING_QA_README.md) độc lập; chọn theo nhiệm vụ, không gọi tất cả hoặc tự nối thành pipeline.

| Skill | Phạm vi phù hợp |
| --- | --- |
| ai-test-design | Thiết kế matrix, ca biên, oracle; không viết/chạy test code. |
| ai-test-automation-review | Review assertion/fixture; viết/chạy tests khi được giao đúng mode. |
| database-consistency-testing | RLS, transaction, CAS, migration, constraints, outbox, concurrency. |
| security-testing | OIDC/JWT/session, CSRF, RBAC, BOLA và scope boundaries. |
| api-contract-testing | Raw DTO/schema parity, errors, SDK và consumer compatibility. |
| reliability-chaos-testing | Fault/restart/lost response trong fixture có ownership/deadline/cleanup. |
| ui-accessibility-testing | Rendered browser, keyboard/focus, assistive-technology semantics. |
| performance-load-testing | Chỉ khi có tải/SLO và môi trường được phép; vài request concurrent không là load qualification. |
| ai-agent-evaluation | Chỉ cho agent/RAG execution được giao; không gọi model để chứng minh CRUD/auth/publish của Bước 01–04. |

Preflight trước thực thi:

- Ghi branch/HEAD/status/staged, owned paths, packet IDs/revisions và migration head. Giữ dirty/untracked changes; không reset/clean/stash/switch hoặc tạo worktree để né chúng.
- Có .codegraph/ thì dùng CodeGraph trước navigation theo AGENTS; không tự index khi không có.
- Phân biệt review, thiết kế, viết test, chạy test và sửa sản phẩm. Prompt test không tự cho phép sửa ứng dụng, deploy hoặc merge.
- Dùng owned PostgreSQL/Redis/SMTP/IdP/browser fixtures, synthetic data và local credentials. Không dùng DSN, account, Redis namespace hoặc volume ứng dụng/production.
- Xác minh ownership trước cleanup/restart. Không reset Docker, xóa volume lạ, terminate toàn bộ WSL hoặc dùng DB ứng dụng làm fallback khi fixture lỗi.
- RLS proof dùng restricted app role không owner/superuser/BYPASSRLS. Migration identity chỉ setup fixture.
- Giới hạn deadline, requests, retries và fault. Không chạy load/chaos hoặc external effects chỉ vì skill có sẵn.
- Action bị tool/approval review từ chối phải ghi BLOCKED và lý do; không đổi tool, agent hoặc cách diễn đạt để chạy lại cùng action bị chặn.

Fixture giả lập dữ liệu không cho phép production trả mock success. Không log/publish raw token, proof, cookie, API key hoặc PII. Auth repro sinh credential fixture mới khi chạy.

## 3. Severity, priority và bằng chứng

Severity mô tả hậu quả; priority P0–P3 là ưu tiên xử lý theo release/context. Không đồng nhất P2 với High hoặc hạ mọi lỗi UI xuống P3.

| Priority gợi ý | Căn cứ |
| --- | --- |
| P0 | Tác động nghiêm trọng đã có bằng chứng, cần ngăn phát hành/khai thác ngay; ví dụ đường ứng dụng thực vượt tenant hoặc mất dữ liệu diện rộng. |
| P1 | Lỗi an toàn/chức năng cốt lõi tác động lớn, điều kiện xảy ra thực tế, cần sửa trước release liên quan. |
| P2 | Lỗi đúng đắn/validation/UX cần sửa trong phạm vi bình thường; nhiều finding04 hiện được review ở mức này. |
| P3 | Tác động thấp, có workaround hợp lý; không là nhãn mặc định cho accessibility. |

Context leak gây đọc tenant khác khác với biến chưa reset nhưng chưa có đường truy cập sai. UI mất nội dung hoặc chặn tác vụ cũng có thể ưu tiên cao. SQL owner-only, unreachable path và giả thuyết chưa tái hiện phải ghi rõ, không tự nâng thành P0.

Báo hai trục:

- **Kết quả chạy:** PASS / FAIL / BLOCKED / NOT_RUN; ghi riêng skipped/deselected và lý do.
- **Loại bằng chứng:** VERIFIED_SOURCE / VERIFIED_TEST / VERIFIED_RUNTIME; phân biệt static, unit/mock, Node VM, actual HTTP/DB và rendered browser.

Probe assert bug vẫn xảy ra có thể exit0 nhưng chỉ có nghĩa REPRODUCED. Regression sau sửa phải assert hành vi đúng. Hypothesis không phải confirmed finding; required skip không là PASS.

## 4. Thiết kế ca và concurrency

BH01-* đến BH04-* là ID cục bộ của playbook, không phải acceptance IDs chính thức. Map sang requirement thật khi triển khai, không tạo mã SPEC giả.

Mỗi ca cần: nguồn oracle/baseline, actor/credential class, setup/data, stimulus, response, state/effects cuối, tầng test, cleanup và phần chưa quyết định. Cột ưu tiên thiết kế là giả định chọn test theo rủi ro (P0/P1/P2), không phải severity/priority của một lỗi đã xác nhận; phải review lại theo scope và release trước chạy. Trong bảng, expected state là bắt buộc; status chưa ghi rõ phải đọc từ route contract.

Concurrency cần lịch quan sát được: barrier/hook chứng minh T1 giữ lock trước khi T2 tới, connection/session độc lập và deadline. Bắn thật nhanh hoặc asyncio.gather chưa chứng minh tranh chấp đúng critical section. Dùng controllable clock cho logic; Redis/PG TTL thực cần authoritative clock/state và polling hữu hạn. Không đổi clock Python rồi giả định Redis đổi theo.

Đọc state cuối bằng transaction/connection mới. Không chỉ tìm FOR SHARE để chứng minh TOCTOU. Kiểm owner writer và producer trong lịch cụ thể; không ép một loại lock nếu cơ chế khác giữ invariant. Deadlock rollback/retry hữu hạn không tự là lỗi toàn vẹn; xét isolation, retry contract và hậu quả.

## 5. Bước 01 — Lưu trữ, migration và RLS

### Các phân biệt bắt buộc

- Thiếu scope phải không lộ/ghi dữ liệu ngoài quyền. So sánh tenant_id với NULL cho unknown trong SQL thông thường, không tự trả toàn bảng; cast UUID từ chuỗi sai có thể raise. Chứng minh fail-open từ policy/privileges/path thực.
- set_config(..., true) là transaction-local. Kiểm context sau commit/rollback/exception/pool reuse; không bắt mọi implementation chạy DISCARD ALL khi transaction boundary đã đúng.
- Gán GUC bằng DB app role kiểm SQL isolation theo trusted scope; không thay HTTP proof rằng client không tự chọn actor/tenant. Owner/superuser bypass không chứng minh actor thường vượt quyền.
- Static validator đọc authored SQL, không kết nối DB thật. Snapshot hiện có 56 baseline + 2 delta = 58 bảng trong 7 schemas; không hardcode 56 hoặc suy 58 đã apply vào DB ứng dụng.
- Runner rerun phải nhận 0 pending khi history đúng. Không buộc từng raw SQL file rerun tùy ý; checksum drift phải deny, không sửa fingerprint/history để test xanh.

### Matrix đề xuất — tất cả NOT_RUN

| ID | Ưu tiên thiết kế | Preconditions / stimulus | Expected response/state | Tầng / nguồn |
| --- | --- | --- | --- | --- |
| BH01-01 | P1 | Restricted role; tenantA workspaceA1/A2, tenantB/B1; seed sentinel từng scope, query hợp lệ đối chứng. | Đọc đúng sentinel, không pass nhờ bảng trống hoặc role sai. | PG + HTTP; RLS/approved scope. |
| BH01-02 | P0 | Thiếu/empty tenant, thiếu/empty workspace, malformed context; SELECT/DML với FK/required fields hợp lệ. | Không read/write vượt quyền. INSERT có thể policy error, UPDATE/DELETE có thể0rows; assert DB không đổi, không buộc mọi deny raise. | Restricted PG; policies/context. |
| BH01-03 | P0 | Đúng tenantA nhưng A2, tenantB/B1; thay ID/path/body từ authenticated request. | API/SQL không tác động/lộ scope khác; status sau authorize theo canonical. | PG + HTTP; auth resolver/RLS. |
| BH01-04 | P0 | Pool nhỏ, chứng minh cùng backend connection; transactionA commit/rollback/exception rồi scopeB/no-context. | Không giữ quyềnA; connection usable hoặc bị loại đúng, không aborted-tx cascade/leak. | Real pool; RLS helper/callers. |
| BH01-05 | P1 | Fresh DB, reviewed upgrade có dữ liệu, runner rerun/drift/fault. | History/DDL đúng, giữ data; 0 pending; drift deny, rollback/advisory lock đúng runner. | Owned PG; migration contract. |
| BH01-06 | P1 | Ghi qua đường thật, restart actual API/server trong owned fixture. | Connection mới đọc cùng data/constraints/RLS; rebuild engine/pool không là server restart. | Process/PG; G1 acceptance. |
| BH01-07 | P1 | Dump fixture, restore DB mới, reconnect app role. | So scoped data/hash/count/FK/triggers/RLS/privileges, replay invariant; không chỉ exit0. | PG recovery; approved scope. |

### Lệnh ứng viên

Chỉ chạy sau preflight và khi được giao VERIFY. Dùng --collect-only -q nếu chưa biết selection; exit5/no tests collected không là PASS. Không chạy các lệnh này khi chỉ sửa playbook.

~~~powershell
uv run --no-sync python -B scripts/validate_database_schema.py
uv run --no-sync pytest tests/contract/test_validate_database_schema.py tests/contract/test_platform_common_rls.py tests/contract/test_migration_tooling.py -q
uv run --no-sync pytest tests/integration/test_rls_requires_context.py tests/integration/test_migration_runner.py tests/integration/test_storage_durability_and_recovery.py tests/integration/test_immutability.py -q
~~~

## 6. Bước 02 — OIDC, session, rotation và revocation

Đọc canonical routes /v1/auth/login, /callback, /me, /refresh, /logout, /token với prefix /v1/auth. Identity ở apps/control-api/src/control_api/modules/identity/; Runtime auth là [agent_runtime/auth.py](../apps/agent-runtime/src/agent_runtime/auth.py), không phải core/auth.py.

Audience/issuer/client ID lấy từ trusted config tương ứng credential; không hardcode cùng audience cho ID Token và application/workspace tokens. ID Token audience là OIDC client theo verifier. Có leeway thì test quanh biên thực, không mặc định exp quá 1 giây luôn reject.

Tách boundary:

- Session revoke đã xác nhận: request mới bắt đầu sau authoritative revocation bị deny khi credential không còn hợp lệ. Request authenticate trước đó xét theo boundary riêng; response muộn không tự là bypass.
- Membership revoke/demotion: request mới lấy authority DB hiện hành, không chờ positive cache hết TTL.
- Stream đang mở theo ADR-021: recheck tối đa 5 giây và trước business data; ghi timestamp/chunk, không hứa thu hồi byte đã gửi.
- Hành động mới: reauthorize trước thực thi; không rollback external effect hợp lệ đã xong.
- Redis/DB lỗi: fail closed theo contract; logout không báo server revocation thành công khi chưa xác nhận. Giữ cookie clearing/idempotent invalid-token logout theo policy.

### Matrix đề xuất — tất cả NOT_RUN

| ID | Ưu tiên thiết kế | Preconditions / stimulus | Expected response/state | Tầng / nguồn |
| --- | --- | --- | --- | --- |
| BH02-01 | P0 | Test IdP/config; login hợp lệ đối chứng; state/nonce/PKCE/callback binding sai. | Đối chứng tạo phiên đúng; input sai không tạo/link phiên trái phép; response/error/audit theo owner. | Crypto + actual OIDC HTTP; AuthSPEC. |
| BH02-02 | P0 | Callback/code consumed, hai login contexts độc lập. | Không reuse code/tạo session trái phép; tách app state consumption/IdP single-use; không revoke mọi phiên khác vì callback lỗi. | IdP/app; OIDC policy. |
| BH02-03 | P0 | Fixture token; alg/signature/iss/aud/kid/claims sai, exp/nbf/iat/leeway ở biên. | Reject đúng credential; sai Bearer không fallback cookie; không leak token. | Verifier/HTTP; trusted config. |
| BH02-04 | P0 | Hai replicas cùng Redis; valid control trước revoke, authoritative ack rồi request mới qua mỗi replica/parent-scoped token. | Credential revoked không truy cập, không false logout success; mockRedis/key tồn tại chưa là distributed proof. | Redis/actual auth; session policy. |
| BH02-05 | P1 | Old/new sessions quanh user cutoff và TTL trước/tại/sau, nhiều replicas. | Old sessions không sống lại; new sessions không revoke nhầm; retention hữu hạn theo lifetime ảnh hưởng. | Redis clock/state; revoke contract. |
| BH02-06 | P1 | Hai refresh cùng credential; replay credential cũ, session khác cùng user. | Atomic rotation, loser theo contract; stale không kéo dài phiên; unrelated session giữ nguyên. | Redis/HTTP; rotation design. |
| BH02-07 | P1 | Idle/absolute ở biên, nhiều refresh, cookie replacement, thiếu/sai CSRF/Origin. | Absolute không tăng; idle/cookie/CSRF đúng; invalid refresh không ghi state/cookie success sai. | Clock/HTTP/Redis/browser; Auth contract. |
| BH02-08 | P1 | Redis outage/timeout verify/logout/rotate, recovery. | Fail closed, không leak secret/hồi sinh token hoặc giả revoke/refresh thành công. | Owned fault; failure contract. |

~~~powershell
uv run --no-sync pytest tests/security/test_authentication_security.py apps/control-api/tests/test_auth_api.py apps/control-api/tests/test_oidc_crypto.py apps/control-api/tests/test_oidc_validation.py -q
uv run --no-sync pytest tests/integration/test_redis_revocation.py tests/integration/test_oidc_e2e_real_postgres.py -q
~~~

## 7. Bước 03 — Workspace, invitation và RBAC

Single-use invitation nghĩa là không có lần kích hoạt quyền nghiệp vụ thứ hai. Canonical cho cùng người nhận hợp lệ đối soát bằng 200 already_accepted; không bắt 4/5 requests phải 409. Replay vẫn kiểm proof/identity/session; revoked membership không được invitation tự phục hồi.

Quyết định đã duyệt cho 03-D: Owner quản lý 5 roles, Admin chỉ Developer/Operator/Viewer; không email-invite Owner; bảo vệ Owner active cuối. Management credential class và expected-role/status preconditions theo contract, không dùng Agent If-Match giả cho membership không có revision đó.

### Matrix đề xuất — tất cả NOT_RUN

| ID | Ưu tiên thiết kế | Preconditions / stimulus | Expected response/state | Tầng / nguồn |
| --- | --- | --- | --- | --- |
| BH03-01 | P0 | Owner/Admin và 5 roles; session/scoped token; active/revoked actor; valid management control. | Enforce role + credential backend/live DB; hidden/disabled UI không thay auth. | HTTP/PG/Redis; ADR-021/03-D. |
| BH03-02 | P0 | Một Owner self-demote/revoke; hai Owners cùng demote/revoke nhau, barrier ở critical lock. | Luôn≥1activeOwner, atomic audit. Lịch đủ preconditions có 1 winner và request kia deny/conflict sau recheck; không ép mọi race bất kỳ đúng 1 success. | PG/HTTP; last-owner invariant. |
| BH03-03 | P1 | Pending invite, verified recipient/proof; nhiều accept cùng user, connection độc lập. | Tối đa1 activation/acceptance business effect/audit; same-user replay có thể trả 200 already_accepted, nhiều response 200 không tự là duplicate write. | HTTP/PG/Redis;03-C contract. |
| BH03-04 | P0 | Sai proof/recipient, identity cũ, expired/revoked invite, user khác, revoked membership. | Không cấp/restore quyền/enumeration;404hidden proof,403identity,409conflict,410expired/revoked chỉ khi proof cho phép theo route. | HTTP/DB;03-C privacy. |
| BH03-05 | P0 | TenantA A1/A2, tenantB B1; sửa path/body/query/resourceIDs trên endpoint families hiện có. | Không read/write ngoài quyền; membership/scope + RLS enforce, gồm cùng tenant khác workspace. | HTTP/restrictedSQL; scope policy. |
| BH03-06 | P0 | Demote/revoke actor lúc writer chờ lock; current stream/event/action; DB/Redis lỗi. | Writer recheck, request mới deny theo DB; stream <= 5s/before-data; action pre-execution deny. Không dựng executor/stream giả để claim wired. | Actual PG/Redis/route; ADR-021. |
| BH03-07 | P1 | Browser workspace/actor switch, late responses, multi-tab logout, lost response rồi refetch. | Clear/abort/ignore scope cũ; không retarget tab khác hoặc blind retry bằng precondition mới/regrant. | Rendered browser/HTTP;03-D UI. |

~~~powershell
uv run --no-sync pytest apps/control-api/tests/test_workspace_memberships_api.py apps/control-api/tests/test_workspace_invitations_api.py apps/control-api/tests/test_invitation_acceptance_unit.py tests/security/test_cross_tenant_isolation.py -q
uv run --no-sync pytest tests/integration/test_workspace_invitations_concurrency_postgres.py tests/integration/test_invitation_acceptance_real_postgres_redis.py tests/integration/test_workspace_rbac_real_postgres_redis.py -q
uv run --no-sync pytest tests/integration/test_invitation_acceptance_browser_e2e.py tests/integration/test_workspace_rbac_browser_e2e.py -q
~~~

File invitations_concurrency có các ca 03-B delivery/lock races; không suy đã bao phủ accept03-C từ tên. Browser cần engine/dependencies riêng; skipped không chứng minh UI đạt.

## 8. Bước 04 — Draft, dependency, publish và promote

### Contract cần giữ

| Boundary | Expected behavior |
| --- | --- |
| Create/PATCH | Raw canonical spec; draft đúng shape có thể giữ unresolved ref. Root extra bị cấm, một số nested cho extension. Không blanket extra=forbid/exclude_none. |
| Ref rỗng | Optional arrays=[] hợp lệ; ref string rỗng/UUID sai khác mảng rỗng, cần semantic diagnostic. Skill object thiếu version vi phạm shape. |
| Giới hạn | DTO create.slug max128 không thành max128 mọi AgentSpec string. temperature thuộc model settings/owner validation, không thêm AgentSpec root để test biên. |
| Prompt có script/chỉ lệnh xấu | Lưu text không chứng minh execution/bypass. Kiểm XSS ở renderer và execution/Tool auth ở boundary thực được giao; không dùng schema như regex chặn mọi câu nghi ngờ. |
| Validate | Semantic readiness khác syntax, không cấp runtimeACL. Unsupported/unavailable/duplicate-conflicting refs không false ready. |
| Publish | Chỉ immutable version/bindings/receipt/audit/outbox; không đổi pointer. Hash không chứa secret/runtime timestamps. |
| Publish replay | Receipt unique (tenant_id, workspace_id, agent_id, operation_key); exact actor/request replay sau current auth, trước stale-draft check. Khác key cùng revision có thể tạo versions tuần tự theo policy. |
| Tool operation | runtime.tool_operations key (tenant_id, workspace_id, operation_key) theo ToolGateway; không thay publish ledger/IAM CRUD bằng nó. |
| Promote/rollback | CAS pointer riêng, current authority/admissibility, sealed target đúng Agent/scope; không sửa history. Environment selection thuộc promote. |
| Model UI | Backend hiện resolve scoped config UUID; alias hardcode không là config đã provision. Không seed model/provider giả để form xanh. |

### Matrix đề xuất — tất cả NOT_RUN

| ID | Ưu tiên thiết kế | Preconditions / stimulus | Expected response/state | Tầng / nguồn |
| --- | --- | --- | --- | --- |
| BH04-01 | P1 | Valid control; required/root extra/nested allowed/null, Skill shape, optional arrays. | Raw parity từng vị trí; create/PATCH422 shape sai; draft unresolved đúng shape vẫn lưu; không normalize invalid thành valid. | Contract/HTTP/PG; AgentSpec/DTO. |
| BH04-02 | P1 | PATCH missing/empty/weak/list/stale If-Match, concurrent same revision; publish/promote bool/string/fraction/overflow, JCS ngoài miền. | PATCH428/400/412 đúng; malformed revision không coerced gây write; dữ liệu không canonicalizable diagnostic/domain422 thay500; không làm tròn spec. | HTTP/PG/JCS; raw type/domain đã review. |
| BH04-03 | P1 | Valid refs; wrongscope/pair/missing/disabled/grant denied; duplicateTool/KB/Skill, hai versions cùngSkill; valid output-schema setting. | Readiness đúng; conflicts reject trước INSERT,422 thay503; property total_tokens không tự là credential; không chọn version/reorder ngầm. | HTTP/PG; owner/UNIQUE; RF04-02/06. |
| BH04-04 | P1 | Same key/request/actor tuần tự/concurrent; replay sau draft edit; same key đổi actor/body. | Một durable effect mỗi intent; authorized replay receipt gốc; changed actor/body409generic; không RAM/pointer/draft-revision mutation. | HTTP/PG; TP04-C. |
| BH04-05 | P1 | Different keys/current revision; publish-vs-edit; dependency writer-vs-publish với barrier. | Versions đúng policy; spec/hash/bindings cùng accepted revision; current grants/config/parent writer serialize/recheck. | PG schedule; C05/C06/C07. |
| BH04-06 | P1 | Fault receipt/audit/outbox/commit sau domain INSERT, normal commit control. | Toàn transaction rollback, không orphan/partial effects/false success; chứng minh fault point chạy. | Actual PG; C08/D09. |
| BH04-07 | P0 | Sealed version/bindings/receipt; fixture role, mutations thuộc approved seal acceptance. | Không phá immutable state qua UPDATE/DELETE/late binding; verify rows/hash. Review authority từng negative probe; probe từng bị chặn giữ BLOCKED, không chạy lại bằng cách khác. | Restricted PG; C10/C11/C12. |
| BH04-08 | P1 | Initial promote0/concurrent promote-rollback/no-op/wrongAgent/scope; builtin parent đổiMCP trước promote. | CAS/stale412/no-op đúng; unsupported kind/grants deny trước pointer; no-op vẫn authorize; history immutable/audit-outbox atomic. | HTTP/PG; D02–D09/RF04-03. |
| BH04-09 | P1 | Browser saveA rồi gõB khi pending,412refetch, unknown/close/reopen publish-promote, history paging, actor/workspace switch, keyboard/alerts. | Không mất edit/state sai scope; exact actor/key/revision recovery, không fake progress; older versions/focus/announcements đúng contract/criterion. | Rendered browser/network; RF04-01/07/08,C16/D10. |
| BH04-10 | P1 | Commit rồi lost response; restart actual API, retry exact key trên instance mới; PG restart/restore fixture. | DB receipt/pointer reconciled, không duplicate; hash/seal/RLS/privileges giữ; không auto retry promote với revision mới. Rebuild engine không thay restart. | Process/PG/HTTP; C09/C14/C15/D08. |

### Regression từ review cuối

RF04 là snapshot finding/evidence, không là bản sửa hoặc phê duyệt semantics mới. Kiểm source trước chạy, giữ evidence cũ; khi được giao sửa, regression assert hành vi đúng.

| Finding | Ca cần giữ |
| --- | --- |
| RF04-01 | Same-Agent edits sau dispatch không mất khi save/refetch về; xét cả parent activeDraft reset. |
| RF04-02 | Bốn duplicate/conflicting combinations; draft vẫn lưu, semantic validate/publish không false ready/503. |
| RF04-03 | Parent kind đổi unsupportedMCP trước promote không tạo pointer. |
| RF04-04 | Malformed revision không coerced thành CAS hợp lệ gây production-pointer write. |
| RF04-05 | Request/spec không canonicalizable không trả 500; lỗi DB thật không bị che thành422. |
| RF04-06 | Output-schema property không tự là credential; credential-bearing config deny theo owner contract. |
| RF04-07 | UI không chọn alias như model thật; mode unsupported rõ ràng, không tự sửa draft cũ khi mở. |
| RF04-08 | Actor đổi cùng workspace reset/refetch; source finding vẫn cần multi-account browser. |
| RF04-09 | A13/A14 không yêu cầu monolith strings/publish stub503; giữ behavior, không skip/xóa để xanh. |

Private probes: artifacts/evidence/final-step04-review-20261117/test_remaining_defects.py và artifacts/evidence/review-all04-20261117/ui-save-race.mjs. Review oracle rồi đưa regression public đúng chỗ khi được giao; không force-add artifacts/chạy subset để né A13/A14.

~~~powershell
uv run --no-sync pytest tests/contract/test_agent_spec_validation.py tests/contract/test_agent_spec_roundtrip.py tests/contract/test_r04c01_seal_fail_closed.py tests/contract/test_r04d_agent_promotions.py -q
uv run --no-sync pytest apps/control-api/tests/test_agent_service.py apps/control-api/tests/test_agent_validation_service.py apps/control-api/tests/test_agent_publish_api.py apps/control-api/tests/test_agent_promotion_api.py apps/control-api/tests/test_if_match_dependency.py -q
uv run --no-sync pytest tests/integration/test_agent_draft_crud_real_postgres.py tests/integration/test_agent_publish_real_postgres.py tests/integration/test_agent_repository_fixes_real_postgres.py -q
node --experimental-strip-types --test apps/studio-web/tests/*.test.ts
pnpm --filter @enterprise/api-client test
pnpm --filter studio-web build
~~~

### Accessibility

Modal giữ Tab/Shift+Tab bên trong là bình thường. Kiểm initial focus, reachable controls, Escape/close và return focus theo pattern cùng unsaved/pending policy đã duyệt. Không tự gán toàn bộ WCAG fail từ một phím trong một trạng thái; ghi criterion, tác vụ bị chặn và keyboard path còn lại. Pending close không là server rollback.

Conflict/error/status phải được assistive technology nhận biết, đủ ngữ cảnh. Không bắt mọi thông báo aria-live=assertive; role/status/politeness tùy mức khẩn cấp. Ghi manual/automated coverage. Node handler, build hoặc tìm role string không thay rendered accessibility proof.

## 9. Prompt mẫu — assignment độc lập

Chọn một bước/requirement/bug cụ thể; không gọi 9 skills cùng lúc. Skill không bắt buộc đầu ra của skill khác.

~~~text
$ai-test-design
Đọc AGENTS và .agents/AI_TESTING_QA_STEPS_01_TO_04.md tại checkout hiện hành.
Mục tiêu: thiết kế tests cho [bước/requirement/finding].
Mode: chỉ tài liệu; không sửa test/sản phẩm, chạy service hoặc security probe.
Đọc approved packet/canonical/source; có .codegraph thì dùng trước navigation.
Ghi baseline, oracle, NEEDS_DECISION; có positive control và negative/boundary/concurrency.
Mỗi case có actor/data/stimulus, expected response+state, tầng/cleanup/NOT_RUN.
Không tự đặt HTTP/TTL/uniqueness hoặc bắt mọi same-user replay đều lỗi.
Giao matrix traceable/uncovered risks; không claim gate PASS/tự chạy skill khác.
~~~

~~~text
$database-consistency-testing
Đọc AGENTS/playbook01–04. Scope: [module/paths/BH cases].
Mode: IMPLEMENT_TESTS + VERIFY; không sửa production/commit/push/merge.
Preflight baseline/dirty tree, packet/constraints/owned paths; giữ changes có sẵn.
Dùng existing owned PG16 Docker fixture/restricted role, không DSN/volume ứng dụng.
Có positive control, barrier/hook/fault point, connection mới để đọc state cuối.
Tách SELECT0rows, UPDATE/DELETE0rows, INSERT policy error; không ép mọi denial raise.
Đo effects/receipt/audit/outbox, không đếm HTTP200 thành business writes.
Cleanup đúng ownership; action từng bị chặn ghi BLOCKED, không bypass.
Báo command/exit/totals/skips/expected-observed/evidence class/NOT_RUN.
Không dùng static/mocks thay runtime, không tự mở production/load/skill khác.
~~~

~~~text
$security-testing
Đọc AGENTS/approved policy/playbook. Scope: [identity/workspace/current Runtime boundary].
Mode: tests trong owned local fixtures; không sửa sản phẩm/dùng account/IdP thật.
Lấy issuer/audience/credential class từ trusted config, không hardcode mọi token.
Có valid request control; kiểm credentials sai/current authority/scope denial/outages.
Tách revoke-ack/new request, membershipDB, stream <= 5s/before-data và action pre-execution.
Dùng shared Redis/PG qua nhiều instances khi cần; mock denylist không là distributed proof.
Không log token/proof/cookie hoặc revoke unrelated sessions vì callback lỗi.
Báo actual evidence/no-effect/NOT_RUN/BLOCKED; không claim remote exploit từ owner SQL.
Không tự sửa policy, chạy skill khác, commit/push/merge.
~~~

~~~text
$api-contract-testing
Scope: [route/schema/consumer] của04; đọc AGENTS/playbook/canonical/callers.
Mode: review + tests khi được giao; không đổi contract/production để test xanh.
Tách draft syntax/readiness/publish/promote; kiểm raw types/bounds/refs/extensions từng vị trí.
Không dùng Tool-operation key thay publish ledger; assert state/effects/safe scope errors.
Ghi mismatch confirmed, NEEDS_DECISION và coverage; không tự chạy skill khác.
~~~

~~~text
$ui-accessibility-testing
Scope: Studio Agent components và [rendered journey]. Đọc AGENTS/playbook/UI contract.
Mode: browser/manual+automated local/test; không sửa sản phẩm nếu chưa giao.
Kiểm DOM/network, keyboard initial/contained/return focus, close/unsaved/pending, announcements.
Tách publish receipt/promote pointer; kiểm model UUID và actor/workspace state.
Ghi criterion/pattern, thao tác, expected/observed, manual/automated coverage.
Thiếu browser/auth fixture thì BLOCKED; build/Node/source scan không là browser PASS.
Không claim toàn bộ WCAG2.2AA/G4 từ một scan hoặc tự chạy skill khác.
~~~

## 10. Báo cáo và điều kiện kết thúc

Lưu review/audit mới tại private docs/09-implementation/ theo task; public clone thiếu bundle dùng vị trí maintainer cung cấp. Giữ báo cáo gốc, không overwrite lịch sử hoặc tự đổi SPEC draft thành approved. Không force-add docs/evidence, commit/push/merge khi chưa giao riêng.

Mỗi finding dùng mẫu:

~~~text
ID: localBH/RF map tới requirement thật; mã mới ghi rõ local.
Baseline: branch/HEAD + dirty hashes, packet/contract revisions.
Component: file:symbol/line, đường caller→effect.
Invariant/oracle: nguồn requirement, phần NEEDS_DECISION.
Actor/environment: credential class/DB role/ownership/versions.
Preconditions: synthetic sentinels và positive control.
Stimulus/schedule: sanitized request/SQL, barriers/fault point/timestamps.
Expected: response + authoritative state/effects và privacy boundary.
Observed: log/số liệu/DB query thực; chưa chạy NOT_RUN.
Countercheck: thử bác bỏ finding; reachable path hay owner-only.
Severity / priority: riêng biệt, impact/likelihood/scope/workaround.
Evidence: VERIFIED_SOURCE / VERIFIED_TEST / VERIFIED_RUNTIME.
Execution: PASS / FAIL / BLOCKED / NOT_RUN; command/exit/totals/skips.
Remediation: smallest fix, contract/authority impact, regression.
Cleanup / remaining gates: nguồn lực đã dọn, recovery và phần chưa kiểm.
~~~

Chỉ thiết kế thì giao matrix/assumptions/source mapping/uncovered risks, execution NOT_RUN. Có chạy thì báo REPORT/VERIFICATION/HANDOFF theo scope, chỉ PASS cho baseline/cases có log thực. Repro không chứa raw credentials; sinh credential fixture lúc chạy.

G1–G4 theo acceptance packet. Một command xanh, container up hoặc tên test chứa browser/restart không đóng gate. G4 vẫn tách rendered browser, actual process restart/lost response, restore/receipt replay, applied history và manifest/capability/environment authority. Future Runtime/Tool/provider chưa wired không được thay mock success để hoàn thành01–04.

## 11. Nguồn kỹ thuật và changelog

- [PostgreSQL comparison/NULL](https://www.postgresql.org/docs/current/functions-comparison.html): ordinary comparison vớiNULL không tự fail-open.
- [OAuth2 RFC6749](https://www.rfc-editor.org/rfc/rfc6749#section-4.1.2): code reuse bị từ chối; xử lý tokens liên quan không tự thành revoke mọi session.
- [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/): contained focus, close, return focus; pattern không là chứng nhận toàn bộWCAG.

Revision2.0 sửa oracle RLS/NULL, invitation replay, revocation, schema/readiness, ledger key, model UUID, accessibility và priority; thêm positive controls/fault/recovery/RF04, sửa đường dẫn/commands/portable links. Không chạy application/integration/browser tests trong lần biên tập; chỉ kiểm paths/links/command collection và bảo toàn source.
