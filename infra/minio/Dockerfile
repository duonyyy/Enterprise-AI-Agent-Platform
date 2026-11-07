# Development/CI image: upstream packaged images are unavailable from the registry.
# Build only pinned official MinIO/MC source; never substitute a local cached image.
FROM golang:1.24.9-alpine3.22 AS builder
RUN apk add --no-cache git ca-certificates
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMEMLIMIT=2048MiB

WORKDIR /src/minio
RUN git init . && git remote add origin https://github.com/minio/minio.git && \
    git fetch --depth 1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a && \
    git checkout --detach 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go build -p 2 -mod=readonly -trimpath -ldflags="-s -w" -o /out/minio .

WORKDIR /src/mc
RUN git init . && git remote add origin https://github.com/minio/mc.git && \
    git fetch --depth 1 origin d6541ea280b73a834b64d4097e21f2be77676104 && \
    git checkout --detach d6541ea280b73a834b64d4097e21f2be77676104
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go build -p 2 -mod=readonly -trimpath -ldflags="-s -w" -o /out/mc .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates && mkdir -p /data /usr/share/licenses/minio /usr/share/licenses/mc
COPY --from=builder /out/minio /out/mc /usr/local/bin/
COPY --from=builder /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
COPY --from=builder /src/mc/LICENSE /usr/share/licenses/mc/LICENSE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z"
ENV MINIO_UPDATE=off
EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
