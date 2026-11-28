"""
Deep Healthcheck Router & Dependency Checker (Track 2 Day 28 IP08 & Day 23 DR).
Implements 3-state health checking (ready, degraded, not_ready) with strict timeout
isolation against frozen processes (SIGSTOP / connection pool exhaustion).
"""

from __future__ import annotations

import concurrent.futures
import enum
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request, Response, status

from agent_runtime.settings import get_settings

router = APIRouter(prefix="/health", tags=["Health & Observability"])


class HealthStatus(enum.StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


@dataclass
class ComponentReport:
    name: str
    status: str  # 'ok' | 'degraded' | 'failed'
    latency_ms: float
    is_critical: bool
    message: str | None = None


class HealthChecker:
    def __init__(self, probe_timeout_seconds: float = 0.5) -> None:
        self.probe_timeout_seconds = probe_timeout_seconds
        self.probes: dict[str, tuple[Callable[[], bool], bool]] = {}

    def register_probe(
        self, name: str, probe_fn: Callable[[], bool], is_critical: bool = True
    ) -> None:
        """Registers a synchronous or lightweight check function."""
        self.probes[name] = (probe_fn, is_critical)

    def _execute_probe_with_timeout(
        self, name: str, probe_fn: Callable[[], bool], is_critical: bool
    ) -> ComponentReport:
        start = time.perf_counter()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(probe_fn)
        try:
            is_ok = future.result(timeout=self.probe_timeout_seconds)
            latency = (time.perf_counter() - start) * 1000.0
            if is_ok:
                return ComponentReport(
                    name=name, status="ok", latency_ms=latency, is_critical=is_critical
                )
            else:
                return ComponentReport(
                    name=name,
                    status="failed" if is_critical else "degraded",
                    latency_ms=latency,
                    is_critical=is_critical,
                    message="Probe returned unhealthy status",
                )
        except concurrent.futures.TimeoutError:
            latency = (time.perf_counter() - start) * 1000.0
            return ComponentReport(
                name=name,
                status="failed" if is_critical else "degraded",
                latency_ms=latency,
                is_critical=is_critical,
                message=f"Probe timed out after {self.probe_timeout_seconds:.2f}s (possible SIGSTOP freeze)",
            )
        except Exception:  # noqa: BLE001 -- isolate arbitrary registered probes without leaking secrets
            latency = (time.perf_counter() - start) * 1000.0
            return ComponentReport(
                name=name,
                status="failed" if is_critical else "degraded",
                latency_ms=latency,
                is_critical=is_critical,
                message="Dependency probe failed",
            )
        finally:
            executor.shutdown(wait=False)

    def check_all(self) -> dict[str, Any]:
        reports: list[ComponentReport] = []
        overall_status = HealthStatus.READY

        for name, (probe_fn, is_critical) in self.probes.items():
            rep = self._execute_probe_with_timeout(name, probe_fn, is_critical)
            reports.append(rep)
            if rep.status != "ok":
                if is_critical:
                    overall_status = HealthStatus.NOT_READY
                elif overall_status != HealthStatus.NOT_READY:
                    overall_status = HealthStatus.DEGRADED

        return {
            "status": overall_status.value,
            "timestamp": time.time(),
            "components": [
                {
                    "name": r.name,
                    "status": r.status,
                    "latency_ms": round(r.latency_ms, 2),
                    "is_critical": r.is_critical,
                    "message": r.message,
                }
                for r in reports
            ],
        }


def probe_postgres() -> bool:
    """Executes a real SELECT 1 query against PostgreSQL using configured credentials."""
    settings = get_settings()
    url = settings.database_url or os.getenv("DATABASE_URL", "")
    if not url:
        return False
    try:
        import psycopg

        clean_url = url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg://", "postgresql://"
        )
        with psycopg.connect(clean_url, connect_timeout=1) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
            return bool(row and row[0] == 1)
    except Exception:  # noqa: BLE001 -- readiness boundary must sanitize driver/config failures
        return False


def probe_redis() -> bool:
    """Use the configured authenticated Redis transport, including TLS when configured."""
    import redis

    try:
        with redis.Redis.from_url(
            get_settings().redis_url, socket_timeout=0.8, socket_connect_timeout=0.8
        ) as client:
            return bool(client.ping())
    except (redis.RedisError, OSError, ValueError):
        return False


def probe_qdrant() -> bool:
    """HTTP readiness probe to Qdrant vector database (/healthz)."""
    settings = get_settings()
    url = settings.qdrant_health_url or os.getenv("QDRANT_HEALTH_URL", "")
    if not url:
        qdrant_host = os.getenv("QDRANT_HOST", "localhost")
        qdrant_port = os.getenv("QDRANT_PORT", "6333")
        url = f"http://{qdrant_host}:{qdrant_port}/healthz"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            return bool(200 <= resp.status < 400)
    except Exception:  # noqa: BLE001 -- readiness boundary must sanitize URL/transport failures
        return False


# Global default healthchecker
default_health_checker = HealthChecker(probe_timeout_seconds=1.5)

# Register real dependency probes
default_health_checker.register_probe("database_postgres", probe_postgres, is_critical=True)
default_health_checker.register_probe("vector_index_qdrant", probe_qdrant, is_critical=True)
default_health_checker.register_probe("semantic_cache_redis", probe_redis, is_critical=True)


def _runtime_health(request: Request) -> dict[str, Any]:
    """Check startup-owned local capabilities; never call a paid model for health."""
    from agent_runtime.llm.gateway import ModelGateway
    from agent_runtime.persistence.object_store import probe_object_store_sync

    result = default_health_checker.check_all()
    gateway = getattr(request.app.state, "model_gateway", None)
    capabilities = sorted(gateway.ready_capabilities) if isinstance(gateway, ModelGateway) else []
    ready = bool(capabilities)
    settings = get_settings()
    object_store = getattr(request.app.state, "object_store", None)
    storage_configured = bool(
        getattr(object_store, "is_configured", False)
        or (
            settings.s3_endpoint
            and settings.s3_access_key
            and settings.s3_secret_key
            and settings.s3_bucket
        )
    )
    storage_timeout = min(settings.dependency_timeout_seconds, 0.8)
    storage_rep = default_health_checker._execute_probe_with_timeout(
        "object_store",
        lambda: probe_object_store_sync(settings, timeout_seconds=storage_timeout),
        is_critical=False,
    )
    storage_ready = storage_rep.status == "ok"
    db_ok = any(
        c["name"] in {"database_postgres", "fixture_db"} and c["status"] == "ok"
        for c in result["components"]
    )
    redis_ok = any(
        c["name"] == "semantic_cache_redis" and c["status"] == "ok" for c in result["components"]
    )
    result["capabilities"] = {
        "admission_ready": bool(db_ok and redis_ok and storage_ready),
        "execution_ready": bool(settings.run_admission_execution_enabled and ready),
        "storage_configured": storage_configured,
        "storage_ready": storage_ready,
        "model_capabilities": capabilities,
    }
    result["components"].append(
        {
            "name": "model_gateway",
            "status": "ok" if ready else "failed",
            "is_critical": True,
            "latency_ms": 0.0,
            "capabilities": capabilities,
            "message": None if ready else "No usable model capability is provisioned",
        }
    )
    if not ready:
        result["status"] = HealthStatus.NOT_READY.value
    return result


@router.get("", summary="Overall health status")
def get_health(request: Request) -> dict[str, Any]:
    res = _runtime_health(request)
    status_val = "ok" if res["status"] in (HealthStatus.READY.value, "ok") else res["status"]
    return {"status": status_val, "health_state": res["status"], "service": "agent-runtime-v2"}


@router.get("/ready", summary="Kubernetes Readiness Probe")
def readiness_probe(response: Response, request: Request) -> dict[str, Any]:
    res = _runtime_health(request)
    if res["status"] == HealthStatus.NOT_READY.value:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        response.status_code = status.HTTP_200_OK
    return res


@router.get("/live", summary="Kubernetes Liveness Probe")
def liveness_probe() -> dict[str, Any]:
    return {"status": "alive", "timestamp": time.time()}


@router.get("/deep", summary="Detailed Health & Latency Report")
def deep_health_probe(response: Response, request: Request) -> dict[str, Any]:
    res = _runtime_health(request)
    if res["status"] == HealthStatus.NOT_READY.value:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return res
