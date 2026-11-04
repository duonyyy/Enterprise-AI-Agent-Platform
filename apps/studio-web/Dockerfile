FROM node:22-alpine AS base

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:$PATH" \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable && corepack prepare pnpm@10.18.3 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages ./packages
COPY apps/studio-web ./apps/studio-web

RUN pnpm install --frozen-lockfile

ARG NEXT_PUBLIC_CONTROL_API_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_RUNTIME_API_URL=http://127.0.0.1:8001
ARG CONTROL_API_INTERNAL_URL=http://control-api:8000

ENV NEXT_PUBLIC_CONTROL_API_URL=${NEXT_PUBLIC_CONTROL_API_URL} \
    NEXT_PUBLIC_RUNTIME_API_URL=${NEXT_PUBLIC_RUNTIME_API_URL} \
    CONTROL_API_INTERNAL_URL=${CONTROL_API_INTERNAL_URL} \
    NODE_ENV=production

RUN pnpm --filter studio-web build && chown -R node:node /app

USER node

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["pnpm", "--filter", "studio-web", "start", "-H", "0.0.0.0", "-p", "3000"]
