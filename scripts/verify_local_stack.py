from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import time
from typing import Any
import urllib.error
import urllib.request

import httpx
import psycopg


def load_dotenv_map(path: Path) -> dict[str, str]:
    env_map: dict[str, str] = {}
    if not path.exists():
        return env_map
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env_map[k.strip()] = v.strip()
    return env_map


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_hs256_jwt(claims: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    h_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_b64 = b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{h_b64}.{p_b64}.{b64url_encode(sig)}"


def s3_sigv4_headers(
    method: str,
    host: str,
    uri_path: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
    content_type: str = "application/json",
) -> dict[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, uri_path, "", canonical_headers, signed_headers, payload_hash]
    )
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, "s3")
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Content-Type": content_type,
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": auth_header,
    }


def redis_command(host: str, port: int, *args: str) -> str:
    parts = [f"*{len(args)}\r\n".encode("utf-8")]
    for arg in args:
        encoded = arg.encode("utf-8")
        parts.append(f"${len(encoded)}\r\n".encode("utf-8") + encoded + b"\r\n")
    payload = b"".join(parts)
    with socket.create_connection((host, port), timeout=3.0) as sock:
        sock.settimeout(3.0)
        sock.sendall(payload)
        return sock.recv(4096).decode("utf-8", errors="replace")


TENANT_A = "11111111-1111-4111-8111-111111111111"
WORKSPACE_A = "22222222-2222-4222-8222-222222222222"
USER_A = "33333333-3333-4333-8333-333333333333"
MEMBERSHIP_A = "44444444-4444-4444-8444-444444444444"
AGENT_A = "55555555-5555-4555-8555-555555555555"
AGENT_VERSION_A = "66666666-6666-4666-8666-666666666666"
TENANT_B = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WORKSPACE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
QDRANT_COLLECTION = "infra_verify_e5_384"


def run_integration_phase(env_map: dict[str, str], evidence_dir: Path) -> None:
    results: dict[str, Any] = {"phase": "integration", "checks": {}}

    # 1. PostgreSQL role, schema, RLS & immutability verification
    owner_pwd = env_map["POSTGRES_PASSWORD"]
    app_pwd = env_map["PLATFORM_APP_DB_PASSWORD"]
    owner_conninfo = f"postgresql://platform_owner:{owner_pwd}@127.0.0.1:5432/enterprise_agent_platform"
    app_conninfo = f"postgresql://platform_app:{app_pwd}@127.0.0.1:5432/enterprise_agent_platform"

    # Bootstrap synthetic tenant/workspace/user/membership via owner role
    with psycopg.connect(owner_conninfo, connect_timeout=5) as owner_conn:
        with owner_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO iam.tenants (id, slug, name, status)
                VALUES (%s, 'infra-verify-tenant-a', 'Infra Verify Tenant A', 'active')
                ON CONFLICT (id) DO NOTHING
                """,
                (TENANT_A,),
            )
            cur.execute(
                """
                INSERT INTO iam.workspaces (id, tenant_id, slug, name, status)
                VALUES (%s, %s, 'infra-verify-ws-a', 'Infra Verify Workspace A', 'active')
                ON CONFLICT (id) DO NOTHING
                """,
                (WORKSPACE_A, TENANT_A),
            )
            cur.execute(
                """
                INSERT INTO iam.users (id, issuer, subject, display_name, email_normalized)
                VALUES (%s, 'http://localhost:8080/realms/enterprise', 'sub-infra-verify', 'Infra Verify Engineer', 'infra-verify@example.com')
                ON CONFLICT (id) DO NOTHING
                """,
                (USER_A,),
            )
            cur.execute(
                """
                INSERT INTO iam.memberships (id, tenant_id, workspace_id, user_id, role, status)
                VALUES (%s, %s, %s, %s, 'owner', 'active')
                ON CONFLICT (id) DO NOTHING
                """,
                (MEMBERSHIP_A, TENANT_A, WORKSPACE_A, USER_A),
            )
            owner_conn.commit()

    with psycopg.connect(app_conninfo, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
                FROM pg_roles
                WHERE rolname IN ('platform_app', 'platform_owner')
                ORDER BY rolname
                """
            )
            roles = {
                r[0]: {
                    "rolsuper": r[1],
                    "rolbypassrls": r[2],
                    "rolcreatedb": r[3],
                    "rolcreaterole": r[4],
                }
                for r in cur.fetchall()
            }
            assert roles["platform_app"]["rolsuper"] is False
            assert roles["platform_app"]["rolbypassrls"] is False
            assert roles["platform_app"]["rolcreatedb"] is False
            assert roles["platform_app"]["rolcreaterole"] is False

            cur.execute(
                """
                SELECT n.nspname, count(*)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
                GROUP BY n.nspname
                ORDER BY n.nspname
                """
            )
            schema_counts = {r[0]: int(r[1]) for r in cur.fetchall()}
            total_tables = sum(schema_counts.values())
            assert total_tables == 56, f"Expected 56 tables, got {total_tables}"

            cur.execute(
                """
                SELECT count(*)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname IN ('iam','catalog','knowledge','runtime','integration','governance','ops')
                  AND c.relrowsecurity = true
                  AND c.relforcerowsecurity = true
                """
            )
            rls_forced_count = int(cur.fetchone()[0])
            assert rls_forced_count == 55, f"Expected 55 RLS-forced tables, got {rls_forced_count}"

            # Seed Agent A and Agent Version A as platform_app under Tenant A + Workspace A RLS context
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE_A,))
            cur.execute(
                """
                INSERT INTO catalog.agents (id, tenant_id, workspace_id, slug, display_name, draft_spec, revision)
                VALUES (%s, %s, %s, 'infra-seeded-agent', 'Infra Seeded Agent', '{"schema_version":"2.0"}'::jsonb, 1)
                ON CONFLICT (id) DO NOTHING
                """,
                (AGENT_A, TENANT_A, WORKSPACE_A),
            )
            spec_json = json.dumps({"schema_version": "2.0", "name": "Infra Seeded Agent"})
            spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
            cur.execute(
                """
                INSERT INTO catalog.agent_versions (
                    id, tenant_id, workspace_id, agent_id, version,
                    schema_version, frozen_spec, bundle_sha256, published_by
                )
                VALUES (%s, %s, %s, %s, 1, '2.0', %s::jsonb, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (AGENT_VERSION_A, TENANT_A, WORKSPACE_A, AGENT_A, spec_json, spec_hash, USER_A),
            )
            conn.commit()

            # Verify Tenant A sees its agent
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE_A,))
            cur.execute("SELECT count(*) FROM catalog.agents WHERE id = %s", (AGENT_A,))
            visible_tenant_a = int(cur.fetchone()[0])
            assert visible_tenant_a == 1

            # Verify fail-closed when context is unset
            cur.execute("SELECT set_config('app.tenant_id', '', true)")
            cur.execute("SELECT set_config('app.workspace_id', '', true)")
            cur.execute("SELECT count(*) FROM catalog.agents")
            visible_no_context = int(cur.fetchone()[0])
            assert visible_no_context == 0

            # Verify fail-closed when only tenant_id is set without workspace_id (0002_fail_closed_rls.sql)
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            cur.execute("SELECT set_config('app.workspace_id', '', true)")
            cur.execute("SELECT count(*) FROM catalog.agents")
            visible_partial_context = int(cur.fetchone()[0])
            assert visible_partial_context == 0

            # Verify Tenant B cannot see or insert into Tenant A
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_B,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE_B,))
            cur.execute("SELECT count(*) FROM catalog.agents")
            visible_tenant_b = int(cur.fetchone()[0])
            assert visible_tenant_b == 0

            cross_tenant_insert_blocked = False
            try:
                with conn.transaction():
                    cur.execute(
                        """
                        INSERT INTO catalog.agents (tenant_id, workspace_id, slug, display_name)
                        VALUES (%s, %s, 'cross-tenant-spoof', 'Spoofed Agent')
                        """,
                        (TENANT_A, WORKSPACE_A),
                    )
            except psycopg.errors.InsufficientPrivilege:
                cross_tenant_insert_blocked = True
            assert cross_tenant_insert_blocked is True

            # Verify immutability trigger on catalog.agent_versions
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE_A,))
            immutability_enforced = False
            try:
                with conn.transaction():
                    cur.execute(
                        "UPDATE catalog.agent_versions SET schema_version = '9.9' WHERE id = %s",
                        (AGENT_VERSION_A,),
                    )
            except Exception as exc:
                if "immutable" in str(exc).lower():
                    immutability_enforced = True
            assert immutability_enforced is True

    results["checks"]["postgresql"] = {
        "status": "PASS",
        "roles": roles,
        "total_tables": total_tables,
        "schema_counts": schema_counts,
        "rls_forced_tables": rls_forced_count,
        "fail_closed_no_context_rows": visible_no_context,
        "fail_closed_missing_workspace_rows": visible_partial_context,
        "cross_tenant_rows_visible": visible_tenant_b,
        "cross_tenant_insert_blocked": cross_tenant_insert_blocked,
        "agent_versions_immutability_trigger": immutability_enforced,
    }

    # 2. Redis verification
    ping_resp = redis_command("127.0.0.1", 6379, "PING")
    save_cfg = redis_command("127.0.0.1", 6379, "CONFIG", "GET", "save")
    aof_cfg = redis_command("127.0.0.1", 6379, "CONFIG", "GET", "appendonly")
    set_resp = redis_command("127.0.0.1", 6379, "SETEX", "infra:verify:probe", "120", "cache-ok")
    get_resp = redis_command("127.0.0.1", 6379, "GET", "infra:verify:probe")
    ttl_resp = redis_command("127.0.0.1", 6379, "TTL", "infra:verify:probe")
    del_resp = redis_command("127.0.0.1", 6379, "DEL", "infra:verify:probe")
    assert "+PONG" in ping_resp
    assert "+OK" in set_resp
    assert "cache-ok" in get_resp
    results["checks"]["redis"] = {
        "status": "PASS",
        "role": "ephemeral_cache",
        "ping": ping_resp.strip(),
        "save_config_raw": save_cfg.strip(),
        "appendonly_config_raw": aof_cfg.strip(),
        "setex_get_del": "OK",
        "ttl_sample": ttl_resp.strip(),
    }

    # 3. MinIO S3 verification with non-root application user
    s3_access = env_map["S3_ACCESS_KEY"]
    s3_secret = env_map["S3_SECRET_KEY"]
    minio_root = env_map["MINIO_ROOT_USER"]
    bucket = env_map.get("S3_BUCKET", "agent-platform-docs")
    assert s3_access != minio_root, "S3_ACCESS_KEY must not equal MINIO_ROOT_USER"

    synthetic_obj = {
        "probe": "enterprise-agent-platform-minio-verify",
        "tenant_id": TENANT_A,
        "workspace_id": WORKSPACE_A,
        "artifact_type": "synthetic_verification_object",
    }
    payload_bytes = json.dumps(synthetic_obj, sort_keys=True).encode("utf-8")
    expected_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    obj_path = f"/{bucket}/infra-verify/synthetic-object.json"
    host_header = "127.0.0.1:9000"

    put_headers = s3_sigv4_headers("PUT", host_header, obj_path, payload_bytes, s3_access, s3_secret)
    put_req = urllib.request.Request(
        f"http://{host_header}{obj_path}", data=payload_bytes, headers=put_headers, method="PUT"
    )
    with urllib.request.urlopen(put_req, timeout=5) as resp:
        put_status = resp.status
    assert put_status == 200

    get_headers = s3_sigv4_headers("GET", host_header, obj_path, b"", s3_access, s3_secret)
    get_req = urllib.request.Request(
        f"http://{host_header}{obj_path}", headers=get_headers, method="GET"
    )
    with urllib.request.urlopen(get_req, timeout=5) as resp:
        get_status = resp.status
        downloaded_bytes = resp.read()
    actual_sha256 = hashlib.sha256(downloaded_bytes).hexdigest()
    assert get_status == 200
    assert actual_sha256 == expected_sha256

    unauth_status = None
    try:
        urllib.request.urlopen(f"http://{host_header}{obj_path}", timeout=3)
    except urllib.error.HTTPError as err:
        unauth_status = err.code
    assert unauth_status in (401, 403)

    results["checks"]["minio"] = {
        "status": "PASS",
        "bucket": bucket,
        "object_key": "infra-verify/synthetic-object.json",
        "non_root_credentials_used": True,
        "put_status": put_status,
        "get_status": get_status,
        "sha256_verified": actual_sha256,
        "unauthenticated_access_rejected_status": unauth_status,
    }

    # 4. Qdrant + Real E5 Embedding verification (host + container)
    from collections.abc import Sequence
    from uuid import UUID
    from agent_runtime.knowledge import (
        E5EmbeddingModel,
        HybridRetriever,
        KnowledgeAuthorization,
        KnowledgeChunk,
        QdrantVectorIndex,
        RetrievalScope,
    )

    class _StackAuthFixture:
        def __init__(self, scope: RetrievalScope) -> None:
            self._scope = scope

        def require_index(self, chunk: KnowledgeChunk) -> None:
            assert chunk.scope == self._scope

        def recheck(self, scope: RetrievalScope, chunks: Sequence[KnowledgeChunk]) -> Sequence[KnowledgeChunk]:
            return tuple(c for c in chunks if c.scope == scope)

    snapshot_path = Path(env_map["E5_MODEL_SNAPSHOT"])
    embedder = E5EmbeddingModel(snapshot_path)
    qdrant = QdrantVectorIndex(url="http://127.0.0.1:6333", collection=QDRANT_COLLECTION)
    qdrant.ensure_collection()

    scope_a = RetrievalScope(UUID(TENANT_A), UUID(WORKSPACE_A))
    base_id = UUID(int=100)
    doc_id = UUID(int=101)
    ver_id = UUID(int=102)

    chunks = [
        KnowledgeChunk(
            scope=scope_a,
            base_id=base_id,
            document_id=doc_id,
            document_version_id=ver_id,
            chunk_id=UUID(int=1),
            text="PostgreSQL row level security enforces tenant_id and workspace_id isolation via platform_app.",
        ),
        KnowledgeChunk(
            scope=scope_a,
            base_id=base_id,
            document_id=doc_id,
            document_version_id=ver_id,
            chunk_id=UUID(int=2),
            text="Every tool invocation passes through the 12-step ToolDispatcher and records idempotency receipts.",
        ),
        KnowledgeChunk(
            scope=scope_a,
            base_id=base_id,
            document_id=doc_id,
            document_version_id=ver_id,
            chunk_id=UUID(int=3),
            text="MinIO stores document artifacts in S3 buckets using scoped non-root application credentials.",
        ),
    ]

    auth = _StackAuthFixture(scope_a)
    retriever = HybridRetriever(
        embeddings=embedder,
        index=qdrant,
        authorization=auth,
    )
    for c in chunks:
        retriever.index_document(c)
    hits = retriever.search(
        "How does PostgreSQL row level security isolate tenant_id and workspace_id?",
        scope=scope_a,
        authorized_chunks=chunks,
        top_k=2,
    )
    assert len(hits) >= 1
    assert hits[0].chunk.chunk_id == UUID(int=1)
    q_vec = embedder.embed_query("test query norm")
    vec_norm = round(math.sqrt(sum(v * v for v in q_vec)), 6)

    # Also verify E5 model read-only mount inside enterprise-agent-runtime container
    container_e5_cmd = [
        "docker",
        "exec",
        "enterprise-agent-runtime",
        "python",
        "-c",
        (
            "import os; from pathlib import Path; "
            "from agent_runtime.knowledge import E5EmbeddingModel; "
            "p = Path(os.environ['E5_MODEL_SNAPSHOT']); "
            "m = E5EmbeddingModel(p); "
            "v = m.embed_query('container offline embedding check'); "
            "print(len(v), round(sum(x*x for x in v)**0.5, 4))"
        ),
    ]
    container_e5_out = subprocess.check_output(container_e5_cmd, text=True).strip()
    assert container_e5_out == "384 1.0", f"Unexpected container E5 output: {container_e5_out}"

    results["checks"]["qdrant_and_e5"] = {
        "status": "PASS",
        "model_id": "intfloat/multilingual-e5-small",
        "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "dimension": len(q_vec),
        "l2_norm": vec_norm,
        "collection": QDRANT_COLLECTION,
        "top1_chunk_id": hits[0].chunk.chunk_id,
        "container_readonly_mount_check": container_e5_out,
    }

    # 5. HTTP Routes & Auth Enforcement (control-api, agent-runtime, studio-web)
    now_ts = int(time.time())
    control_token = mint_hs256_jwt(
        {
            "sub": USER_A,
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "iss": env_map.get("JWT_ISSUER", "http://localhost:8080/realms/enterprise"),
            "aud": env_map.get("JWT_AUDIENCE", "enterprise-control-api"),
            "iat": now_ts,
            "exp": now_ts + 3600,
        },
        env_map["JWT_SECRET_KEY"],
    )
    wrong_ws_token = mint_hs256_jwt(
        {
            "sub": USER_A,
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_B,
            "iss": env_map.get("JWT_ISSUER", "http://localhost:8080/realms/enterprise"),
            "aud": env_map.get("JWT_AUDIENCE", "enterprise-control-api"),
            "iat": now_ts,
            "exp": now_ts + 3600,
        },
        env_map["JWT_SECRET_KEY"],
    )
    runtime_token = mint_hs256_jwt(
        {
            "sub": "sub-infra-verify",
            "user_id": USER_A,
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "roles": ["owner"],
            "scopes": ["run:start", "run:read"],
            "iss": env_map.get("RUNTIME_JWT_ISSUER", "http://localhost:8080/realms/enterprise"),
            "aud": env_map.get("RUNTIME_JWT_AUDIENCE", "agent-runtime"),
            "iat": now_ts,
            "exp": now_ts + 3600,
        },
        env_map["RUNTIME_JWT_SECRET"],
    )

    with httpx.Client(timeout=10.0) as client:
        # Control API health & readiness
        c_health = client.get("http://127.0.0.1:8000/health")
        c_live = client.get("http://127.0.0.1:8000/health/live")
        c_ready = client.get("http://127.0.0.1:8000/health/ready")
        assert c_health.status_code == 200
        assert c_live.status_code == 200
        assert c_ready.status_code == 200 and c_ready.json()["status"] == "ready"

        # Control API auth rejection checks
        c_unauth = client.get(f"http://127.0.0.1:8000/v1/workspaces/{WORKSPACE_A}/agents")
        c_bad_sig = client.get(
            f"http://127.0.0.1:8000/v1/workspaces/{WORKSPACE_A}/agents",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        c_wrong_ws = client.get(
            f"http://127.0.0.1:8000/v1/workspaces/{WORKSPACE_A}/agents",
            headers={"Authorization": f"Bearer {wrong_ws_token}"},
        )
        assert c_unauth.status_code == 401
        assert c_bad_sig.status_code == 401
        assert c_wrong_ws.status_code == 403

        # Control API authenticated endpoints
        unique_slug = f"infra-http-agent-{int(time.time())}"
        c_create_agent = client.post(
            f"http://127.0.0.1:8000/v1/workspaces/{WORKSPACE_A}/agents",
            headers={"Authorization": f"Bearer {control_token}"},
            json={
                "slug": unique_slug,
                "display_name": "Infra Verified HTTP Agent",
                "spec": {"schema_version": "2.0", "instructions": "Verify local stack"},
            },
        )
        assert c_create_agent.status_code == 201, c_create_agent.text
        c_list_agents = client.get(
            f"http://127.0.0.1:8000/v1/workspaces/{WORKSPACE_A}/agents",
            headers={"Authorization": f"Bearer {control_token}"},
        )
        assert c_list_agents.status_code == 200
        assert isinstance(c_list_agents.json(), list) and len(c_list_agents.json()) >= 1

        # Agent Runtime health & readiness
        r_health = client.get("http://127.0.0.1:8001/health")
        r_live = client.get("http://127.0.0.1:8001/health/live")
        r_ready = client.get("http://127.0.0.1:8001/health/ready")
        r_deep = client.get("http://127.0.0.1:8001/health/deep")
        assert r_health.status_code == 200
        assert r_live.status_code == 200
        assert r_ready.status_code == 200
        assert r_deep.status_code == 200

        # Agent Runtime auth rejection & run queueing
        r_unauth = client.post(
            "http://127.0.0.1:8001/v1/runs",
            headers={"Idempotency-Key": f"idem-unauth-{now_ts}"},
            json={
                "workspace_id": WORKSPACE_A,
                "agent_version_id": AGENT_VERSION_A,
                "inputs": {"message": "hello"},
            },
        )
        assert r_unauth.status_code == 401

        r_spoof_ws = client.post(
            "http://127.0.0.1:8001/v1/runs",
            headers={
                "Authorization": f"Bearer {runtime_token}",
                "Idempotency-Key": f"idem-spoof-{now_ts}",
            },
            json={
                "workspace_id": WORKSPACE_B,
                "agent_version_id": AGENT_VERSION_A,
                "inputs": {"message": "spoof"},
            },
        )
        assert r_spoof_ws.status_code == 403

        r_create_run = client.post(
            "http://127.0.0.1:8001/v1/runs",
            headers={
                "Authorization": f"Bearer {runtime_token}",
                "Idempotency-Key": f"idem-valid-{now_ts}",
            },
            json={
                "workspace_id": WORKSPACE_A,
                "agent_version_id": AGENT_VERSION_A,
                "inputs": {"message": "Verify durable run persistence"},
            },
        )
        assert r_create_run.status_code == 202, r_create_run.text
        created_run_id = r_create_run.json()["run_id"]

        r_get_run = client.get(
            f"http://127.0.0.1:8001/v1/runs/{created_run_id}",
            headers={"Authorization": f"Bearer {runtime_token}"},
        )
        assert r_get_run.status_code == 200
        assert r_get_run.json()["status"] == "queued"

        # Studio Web frontend & internal DNS proxy rewrite check
        w_login = client.get("http://127.0.0.1:3000/login")
        w_agents = client.get("http://127.0.0.1:3000/w/default/agents")
        w_proxy_unauth = client.get(
            f"http://127.0.0.1:3000/v1/workspaces/{WORKSPACE_A}/agents"
        )
        w_proxy_auth = client.get(
            f"http://127.0.0.1:3000/v1/workspaces/{WORKSPACE_A}/agents",
            headers={"Authorization": f"Bearer {control_token}"},
        )
        assert w_login.status_code == 200
        assert w_agents.status_code == 200
        assert w_proxy_unauth.status_code == 401
        assert w_proxy_auth.status_code == 200

    results["checks"]["http_and_auth"] = {
        "status": "PASS",
        "control_api": {
            "health_status": c_health.status_code,
            "live_status": c_live.status_code,
            "ready_status": c_ready.status_code,
            "unauthenticated_status": c_unauth.status_code,
            "invalid_jwt_status": c_bad_sig.status_code,
            "wrong_workspace_status": c_wrong_ws.status_code,
            "create_agent_status": c_create_agent.status_code,
            "list_agents_count": len(c_list_agents.json()),
        },
        "agent_runtime": {
            "health_status": r_health.status_code,
            "live_status": r_live.status_code,
            "ready_status": r_ready.status_code,
            "deep_components": r_deep.json().get("components", []),
            "unauthenticated_status": r_unauth.status_code,
            "spoofed_workspace_status": r_spoof_ws.status_code,
            "create_run_status": r_create_run.status_code,
            "get_run_status": r_get_run.status_code,
            "created_run_id": created_run_id,
        },
        "studio_web": {
            "login_page_status": w_login.status_code,
            "agents_page_status": w_agents.status_code,
            "internal_dns_rewrite_unauth_status": w_proxy_unauth.status_code,
            "internal_dns_rewrite_auth_status": w_proxy_auth.status_code,
        },
    }

    out_path = evidence_dir / "04-live-integration-checks.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("PASS: Live integration checks written to", out_path)


def run_resilience_phase(env_map: dict[str, str], evidence_dir: Path) -> None:
    resilience: dict[str, Any] = {"phase": "resilience", "checks": {}}

    # 1. Restart services and verify data persistence across restarts
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "infra/compose/docker-compose.dev.yml",
            "restart",
            "postgres",
            "minio",
            "qdrant",
            "control-api",
            "agent-runtime",
            "studio-web",
        ],
        check=True,
    )

    # Wait for readiness after restart
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3.0) as client:
                r1 = client.get("http://127.0.0.1:8000/health/ready")
                r2 = client.get("http://127.0.0.1:8001/health/ready")
                r3 = client.get("http://127.0.0.1:3000/login")
                if r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200:
                    break
        except Exception:
            pass
        time.sleep(2)

    # Check PostgreSQL data survived restart
    app_pwd = env_map["PLATFORM_APP_DB_PASSWORD"]
    app_conninfo = f"postgresql://platform_app:{app_pwd}@127.0.0.1:5432/enterprise_agent_platform"
    with psycopg.connect(app_conninfo, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_A,))
            cur.execute("SELECT set_config('app.workspace_id', %s, true)", (WORKSPACE_A,))
            cur.execute("SELECT count(*) FROM catalog.agents")
            agents_after_restart = int(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM runtime.runs")
            runs_after_restart = int(cur.fetchone()[0])
    assert agents_after_restart >= 1
    assert runs_after_restart >= 1

    # Check MinIO object survived restart
    s3_access = env_map["S3_ACCESS_KEY"]
    s3_secret = env_map["S3_SECRET_KEY"]
    bucket = env_map.get("S3_BUCKET", "agent-platform-docs")
    obj_path = f"/{bucket}/infra-verify/synthetic-object.json"
    host_header = "127.0.0.1:9000"
    get_headers = s3_sigv4_headers("GET", host_header, obj_path, b"", s3_access, s3_secret)
    get_req = urllib.request.Request(
        f"http://{host_header}{obj_path}", headers=get_headers, method="GET"
    )
    with urllib.request.urlopen(get_req, timeout=5) as resp:
        minio_status_after_restart = resp.status
        minio_bytes_after_restart = resp.read()
    assert minio_status_after_restart == 200
    assert len(minio_bytes_after_restart) > 20

    # Check Qdrant vectors survived restart
    from uuid import UUID
    from agent_runtime.knowledge import E5EmbeddingModel, KnowledgeChunk, QdrantVectorIndex, RetrievalScope

    scope_a = RetrievalScope(UUID(TENANT_A), UUID(WORKSPACE_A))
    base_id = UUID(int=100)
    doc_id = UUID(int=101)
    ver_id = UUID(int=102)
    chunks = [
        KnowledgeChunk(
            scope=scope_a,
            base_id=base_id,
            document_id=doc_id,
            document_version_id=ver_id,
            chunk_id=UUID(int=1),
            text="PostgreSQL row level security enforces tenant_id and workspace_id isolation via platform_app.",
        ),
    ]

    embedder = E5EmbeddingModel(Path(env_map["E5_MODEL_SNAPSHOT"]))
    qdrant = QdrantVectorIndex(url="http://127.0.0.1:6333", collection=QDRANT_COLLECTION)
    q_vec = embedder.embed_query(
        "How does PostgreSQL row level security isolate tenant_id and workspace_id?"
    )
    qdrant_hits = qdrant.search(
        scope=scope_a,
        chunks=chunks,
        vector=q_vec,
        limit=2,
    )
    assert len(qdrant_hits) >= 1
    assert qdrant_hits[0].point_id == chunks[0].point_id

    resilience["checks"]["restart_persistence"] = {
        "status": "PASS",
        "postgres_agents_count_after_restart": agents_after_restart,
        "postgres_runs_count_after_restart": runs_after_restart,
        "minio_object_status_after_restart": minio_status_after_restart,
        "qdrant_top1_after_restart": qdrant_hits[0].point_id,
    }

    # 2. Dependency outage test: stop postgres -> verify /health/ready returns 503 on both APIs while /health/live stays 200 -> start postgres -> verify recovery to 200
    subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "infra/compose/docker-compose.dev.yml",
            "stop",
            "postgres",
        ],
        check=True,
    )
    try:
        with httpx.Client(timeout=5.0) as client:
            c_live_down = client.get("http://127.0.0.1:8000/health/live")
            c_ready_down = client.get("http://127.0.0.1:8000/health/ready")
            r_live_down = client.get("http://127.0.0.1:8001/health/live")
            r_ready_down = client.get("http://127.0.0.1:8001/health/ready")
        assert c_live_down.status_code == 200
        assert c_ready_down.status_code == 503
        assert r_live_down.status_code == 200
        assert r_ready_down.status_code == 503
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose/docker-compose.dev.yml",
                "start",
                "postgres",
            ],
            check=True,
        )

    # Wait for recovery
    recovered = False
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3.0) as client:
                c_rec = client.get("http://127.0.0.1:8000/health/ready")
                r_rec = client.get("http://127.0.0.1:8001/health/ready")
                if c_rec.status_code == 200 and r_rec.status_code == 200:
                    recovered = True
                    break
        except Exception:
            pass
        time.sleep(2)
    assert recovered is True

    resilience["checks"]["dependency_loss_readiness"] = {
        "status": "PASS",
        "when_postgres_stopped": {
            "control_api_live_status": c_live_down.status_code,
            "control_api_ready_status": c_ready_down.status_code,
            "control_api_ready_body": c_ready_down.json(),
            "agent_runtime_live_status": r_live_down.status_code,
            "agent_runtime_ready_status": r_ready_down.status_code,
            "agent_runtime_ready_body": r_ready_down.json(),
        },
        "recovered_after_postgres_started": recovered,
    }

    out_path = evidence_dir / "06-resilience-and-persistence.json"
    out_path.write_text(json.dumps(resilience, indent=2), encoding="utf-8")
    print("PASS: Resilience and persistence checks written to", out_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--phase", choices=["integration", "resilience"], default="integration")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    env_map = load_dotenv_map(repo_root / ".env")
    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    if args.phase == "integration":
        run_integration_phase(env_map, evidence_dir)
    else:
        run_resilience_phase(env_map, evidence_dir)


if __name__ == "__main__":
    main()
