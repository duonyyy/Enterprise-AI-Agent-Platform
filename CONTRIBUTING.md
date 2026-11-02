# Contributing

## Local bootstrap

Read [`docs/00-governance/AGENT_COLLABORATION_AND_BRANCHING.md`](./docs/00-governance/AGENT_COLLABORATION_AND_BRANCHING.md) before creating a branch or opening a PR.

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
uv run python docs/03-database/database/validate_db_static.py
pnpm --filter studio-web build
```

5. Chạy dependency stack bằng `docker compose -f infra/compose/docker-compose.dev.yml up --build` sau khi Docker Desktop hoạt động. Worker là profile riêng; chỉ bật khi có `WORKER_TENANT_ID` và `WORKER_WORKSPACE_ID` hợp lệ.

## Rules

- Không sửa `0001_baseline.sql` sau khi môi trường đã áp dụng migration; thêm forward migration.
- Không thêm secret, `.env`, node_modules hay artefact build vào Git.
- Dùng `uv lock` và `pnpm install --lockfile-only` mỗi khi đổi dependency; commit cả `uv.lock` và `pnpm-lock.yaml`.
- Dùng Conventional Commits, ví dụ: `feat(runtime): persist checkpoints` hoặc `fix(control-api): reject stale approval`.
- Mọi side effect phải qua Tool Gateway; runtime không được thêm fallback RAM/mock success.
- Báo cáo kết quả kiểm thử là `PASS`, `FAIL`, `NOT_RUN` hoặc `BLOCKED` kèm lệnh chạy.
- Dùng một nhánh và một PR nhỏ cho một outcome; không push trực tiếp vào `main`.
