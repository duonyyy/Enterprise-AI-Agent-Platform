"""Storage fixtures only: real object/vector persistence, not RAG or IAM proof."""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx
import pytest
from test_postgres_fixture import LABEL, docker, verify_owned
from verify_local_stack import s3_sigv4_headers

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def owned_service(image, port, target, command=(), env=()):
    run_id = uuid4().hex
    resource = {"run_id": run_id, "volume": f"step01-storage-{run_id}"}
    evidence = Path(__file__).resolve().parents[2] / "artifacts/evidence/step01-fixtures" / run_id
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "OWNERSHIP.json").write_text(
        json.dumps({**resource, "image": image, "internal_port": port, "mount": target}, indent=2)
    )
    docker("volume", "create", "--label", f"{LABEL}={run_id}", resource["volume"])
    args = [
        "--label",
        f"{LABEL}={run_id}",
        "--publish",
        f"127.0.0.1::{port}",
        "--mount",
        f"type=volume,source={resource['volume']},target={target}",
    ]
    for value in env:
        args.extend(["--env", value])

    def start():
        resource["container_id"] = docker("create", *args, image, *command)
        verify_owned(resource)
        docker("start", resource["container_id"])

    resource["start"] = start
    resource["port"] = port
    try:
        start()
        yield resource
    finally:
        if "container_id" in resource:
            verify_owned(resource)
            docker("rm", "--force", resource["container_id"])
        verify_owned(resource, volume=True)
        docker("volume", "rm", resource["volume"])
        (evidence / "CLEANUP.txt").write_text("PASS: removed owned container and volume only.\n")


def endpoint(resource, health):
    ports = json.loads(
        docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", resource["container_id"])
    )
    binding = ports[f"{resource['port']}/tcp"][0]
    assert binding["HostIp"] == "127.0.0.1"
    url = f"http://127.0.0.1:{binding['HostPort']}"
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            if httpx.get(url + health, timeout=2).status_code == 200:
                return url
        except httpx.TransportError:
            pass
        Event().wait(0.3)
    raise AssertionError("Owned storage fixture did not become ready")


def cycle(resource, recreate):
    verify_owned(resource)
    verify_owned(resource, volume=True)
    if recreate:
        docker("rm", "--force", resource["container_id"])
        del resource["container_id"]
        resource["start"]()
    else:
        docker("restart", resource["container_id"])


def test_minio_object_and_app_identity_survive_restart_and_recreate():
    root_secret, app_secret = secrets.token_hex(24), secrets.token_hex(24)
    with owned_service(
        "enterprise-agent-platform-minio:2025-10-15",
        9000,
        "/data",
        ("server", "/data"),
        ("MINIO_ROOT_USER=fixture_root", f"MINIO_ROOT_PASSWORD={root_secret}"),
    ) as service:
        url = endpoint(service, "/minio/health/ready")
        container = service["container_id"]
        # Same health command as Compose, before any fixture alias is configured.
        docker("exec", container, "mc", "ready", "local")
        docker(
            "exec",
            container,
            "mc",
            "alias",
            "set",
            "fixture",
            "http://127.0.0.1:9000",
            "fixture_root",
            root_secret,
        )
        docker(
            "exec", container, "mc", "admin", "user", "add", "fixture", "fixture_app", app_secret
        )
        docker(
            "exec",
            container,
            "mc",
            "admin",
            "policy",
            "attach",
            "fixture",
            "readwrite",
            "--user",
            "fixture_app",
        )

        def request(method, path, body=b""):
            host = url.removeprefix("http://")
            headers = s3_sigv4_headers(
                method,
                host,
                path,
                body,
                "fixture_app",
                app_secret,
                content_type="application/octet-stream",
            )
            return httpx.request(method, url + path, headers=headers, content=body, timeout=5)

        request("PUT", "/step01-fixture").raise_for_status()
        content = "Tài liệu lưu bền vững — fixture, không phải dữ liệu nghiệp vụ.".encode()
        request("PUT", "/step01-fixture/source.txt", content).raise_for_status()
        for recreate in (False, True):
            cycle(service, recreate)
            url = endpoint(service, "/minio/health/ready")
            response = request("GET", "/step01-fixture/source.txt")
            response.raise_for_status()
            assert response.content == content


def test_qdrant_vector_and_payload_survive_restart_and_recreate():
    with owned_service("qdrant/qdrant:v1.9.0", 6333, "/qdrant/storage") as service:
        url = endpoint(service, "/readyz")
        vector = [1.0] + [0.0] * 383
        payload = {
            "tenant_id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "chunk_id": str(uuid4()),
        }
        httpx.put(
            url + "/collections/step01", json={"vectors": {"size": 384, "distance": "Cosine"}}
        ).raise_for_status()
        httpx.put(
            url + "/collections/step01/points?wait=true",
            json={"points": [{"id": 1, "vector": vector, "payload": payload}]},
        ).raise_for_status()
        for recreate in (False, True):
            cycle(service, recreate)
            url = endpoint(service, "/readyz")
            response = httpx.get(url + "/collections/step01/points/1?with_vector=true", timeout=5)
            response.raise_for_status()
            point = response.json()["result"]
            assert point["payload"] == payload
            assert point["vector"] == pytest.approx(vector)
