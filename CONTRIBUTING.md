# Contributing

## Local bootstrap

Read [AGENTS.md](./AGENTS.md), [coding rules](./AGENT_CODING_RULES.md) and the [architecture overview](./ARCHITECTURE.md) before editing. These public guides work without the private documentation directory.

1. Cài Python 3.12+, `uv 0.11.26`, Node.js 22, pnpm 10.18.3 và Docker Desktop.
2. Copy `.env.example` thành `.env`; thay tất cả giá trị `replace-with-*` bằng secret development riêng. Không commit file này.
3. Cài dependency đúng lockfile:

```powershell
uv sync --all-packages --locked --group dev
pnpm install --frozen-lockfile
```

4. Kiểm tra trước khi gửi PR:

```powershell
uv run python -B -m compileall -q apps libs tests
uv run pytest tests/contract/ apps/control-api/tests apps/jobs-worker/tests -v
uv run python -B scripts/validate_database_schema.py
pnpm --filter studio-web build
```

5. Chạy dependency stack bằng `docker compose -f infra/compose/docker-compose.dev.yml up --build` sau khi Docker Desktop hoạt động. Worker là profile riêng; chỉ bật khi có `WORKER_TENANT_ID` và `WORKER_WORKSPACE_ID` hợp lệ.

The static database validator recognizes baseline declarations after masking SQL
comments and quoted spans. It pins the reviewed `0002` program by a lexical
fingerprint; it does not execute or semantically validate PL/pgSQL. The current
authored `0002` leaves `iam.workspaces` without its policy, a known Step 01 blocker.
Static PASS does not close PostgreSQL/RLS, privileges, restore or upgrade gates.
Reviewed forward migrations require an explicit update to the static migration
inventory and relevant tests; do not edit applied migrations or refresh a
fingerprint merely to suppress a failure.

## Rules

- Không sửa `0001_baseline.sql` sau khi môi trường đã áp dụng migration; thêm forward migration.
- Không thêm secret, `.env`, node_modules hay artefact build vào Git.
- Dùng `uv lock` và `pnpm install --lockfile-only` mỗi khi đổi dependency; commit cả `uv.lock` và `pnpm-lock.yaml`.
- Dùng Conventional Commits, ví dụ: `feat(runtime): persist checkpoints` hoặc `fix(control-api): reject stale approval`.
- Mọi side effect phải qua Tool Gateway; runtime không được thêm fallback RAM/mock success.
- Báo cáo kết quả kiểm thử là `PASS`, `FAIL`, `NOT_RUN` hoặc `BLOCKED` kèm lệnh chạy.
- Dùng một nhánh và một PR nhỏ cho một outcome; không push trực tiếp vào `main`.

## Branches, ownership and review

- `main` là nhánh tích hợp dài hạn. Mỗi công việc dùng nhánh ngắn `feat/...`, `fix/...`, `test/...`, `chore/...` hoặc `codex/...` cho agent; không tạo nhánh `develop` dài hạn mặc định.
- Bắt đầu từ integration SHA đã được maintainer chấp nhận. Kiểm branch, HEAD, status và staged changes trước khi làm. Không switch, reset, clean hoặc stash để xử lý một working tree đang có thay đổi của người khác.
- Khai báo outcome và owned paths trong PR. Chỉ một người/PR sửa một contract hoặc migration chain tại một thời điểm; thống nhất thứ tự tích hợp trước khi thay schema/API chung.
- Không import nội bộ chéo giữa Control API và Runtime. Domain phụ thuộc ports/contracts; adapter/framework được nối tại startup.
- Maintainer cấu hình GitHub yêu cầu PR, human review, checks liên quan đạt và review conversations được giải quyết trước merge. Đây là yêu cầu workflow, không phải khẳng định branch protection đã bật.
- Xóa nhánh công việc sau merge. Không commit/push, deploy hay sửa quyền ngoài phạm vi được giao.

## Private specifications and task packets

Use cases, full approved coding policy, design decisions/ADRs, audit reports, evidence and the implementation runbook are maintained separately. They are not required to run the public static validator or build tools. Feature implementation still requires the relevant approved requirements.

Before assigning a feature, the maintainer supplies a task packet containing:

1. Outcome/use-case ID and exact code baseline.
2. Applicable approved SPEC/policy/ADR/CR IDs and revisions (or approved excerpts), with a private access location.
3. Decisions still open, owned paths and contract/migration impact.
4. Acceptance tests, environment/authority needed, rollback and evidence requirements.

Không có nội dung yêu cầu bắt buộc thì báo BLOCKED phần phụ thuộc, không tự suy endpoint, schema hoặc quyền từ README. Các file local có thể đặt trong thư mục `docs/` đã ignore; việc có file không chứng minh nó đã được duyệt. Không copy tài liệu nội bộ vào public issue/PR hoặc ép thêm bằng `git add -f`.

Local handoff snapshots and dedicated worktrees can be kept under `.private-docs-export/`, which is excluded from Git and the Docker build context alongside `docs/` and `artifacts/evidence/`. Original files remain in place. Treat any local snapshot as private material and review it before sharing. To preserve relative links to source/contracts while working locally, use the documentation as an ignored overlay in the code checkout. When copying updated docs, reconcile local edits rather than overwriting them blindly.

## Evidence and publication

Use `.github/pull_request_template.md`. Report PASS/FAIL/NOT_RUN/BLOCKED with exact commands, environment and what the test actually proves. Static SQL inventory is not PostgreSQL execution/RLS evidence; unit/mock tests are not recovery or provider proof. Never put raw prompts, secrets or tenant data in public artifacts.

Public changes include source, contracts, canonical migrations, tests, infrastructure, scripts and the small root-level guides. Internal material under `docs/`, local private exports and evidence stays out of the public source tree and Docker context. Check the staged diff before committing.

Untracking a path and adding ignore rules affect future commits. They do not remove copies already present in old commits, remote branches, forks or downloads. Do not rewrite shared history or force-push without a separately approved migration plan.
