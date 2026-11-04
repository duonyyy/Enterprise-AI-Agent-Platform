"""
Deep Healthcheck Router & Dependency Checker (Track 2 Day 28 IP08 & Day 23 DR).
Implements 3-state health checking (ready, degraded, not_ready) with strict timeout
isolation against frozen processes (SIGSTOP / connection pool exhaustion).
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import enum
import time
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Response, status

router = APIRouter(prefix="/health", tags=["Health & Observability"])


class HealthStatus(str, enum.Enum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


@dataclass
class ComponentReport:
    name: str
    status: str  # 'ok' | 'degraded' | 'failed'
    latency_ms: float
    is_critical: bool
    message: Optional[str] = None


class HealthChecker:
    def __init__(self, probe_timeout_seconds: float = 0.5):
        self.probe_timeout_seconds = probe_timeout_seconds
        self.probes: Dict[str, Tuple[Callable[[], bool], bool]] = {}

    def register_probe(self, name: str, probe_fn: Callable[[], bool], is_critical: bool = True):
        """Registers a synchronous or lightweight check function."""
        self.probes[name] = (probe_fn, is_critical)

    def _execute_probe_with_timeout(self, name: str, probe_fn: Callable[[], bool], is_critical: bool) -> ComponentReport:
        start = time.perf_counter()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(probe_fn)
        try:
            is_ok = future.result(timeout=self.probe_timeout_seconds)
            latency = (time.perf_counter() - start) * 1000.0
            if is_ok:
                return ComponentReport(name=name, status="ok", latency_ms=latency, is_critical=is_critical)
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
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000.0
            return ComponentReport(
                name=name,
                status="failed" if is_critical else "degraded",
                latency_ms=latency,
                is_critical=is_critical,
                message=str(exc),
            )
        finally:
            executor.shutdown(wait=False)

    def check_all(self) -> Dict[str, Any]:
        reports: List[ComponentReport] = []
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


import os
import socket
import urllib.request
from urllib.parse import urlparse

from agent_runtime.settings import get_settings


def probe_postgres() -> bool:
    """Executes a real SELECT 1 query against PostgreSQL using configured credentials."""
    settings = get_settings()
    url = settings.database_url or os.getenv("DATABASE_URL", "")
    if not url:
        return False
    try:
        import psycopg

        clean_url = (
            url.replace("postgresql+asyncpg://", "postgresql://")
            .replace("postgresql+psycopg://", "postgresql://")
        )
        with psycopg.connect(clean_url, connect_timeout=1) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return bool(row and row[0] == 1)
    except Exception:
        return False


def probe_redis() -> bool:
    """Protocol-level PING/PONG probe to Redis server."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=0.8) as sock:
            sock.settimeout(0.8)
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            data = sock.recv(64)
            return data.startswith(b"+PONG")
    except Exception:
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
            return 200 <= resp.status < 400
    except Exception:
        return False


def probe_model_gateway() -> bool:
    """Probes the explicitly configured model gateway URL if present."""
    settings = get_settings()
    url = settings.model_gateway_health_url or os.getenv("MODEL_GATEWAY_URL", "")
    if not url:
        return False
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


# Global default healthchecker
default_health_checker = HealthChecker(probe_timeout_seconds=1.5)

# Register real dependency probes
default_health_checker.register_probe("database_postgres", probe_postgres, is_critical=True)
default_health_checker.register_probe("vector_index_qdrant", probe_qdrant, is_critical=True)
default_health_checker.register_probe("semantic_cache_redis", probe_redis, is_critical=False)
default_health_checker.register_probe(
    "model_gateway",
    probe_model_gateway,
    is_critical=bool(get_settings().model_gateway_health_url or os.getenv("MODEL_GATEWAY_URL", "")),
)


@router.get("", summary="Overall health status")
def get_health():
    res = default_health_checker.check_all()
    status_val = "ok" if res["status"] in (HealthStatus.READY.value, "ok") else res["status"]
    return {"status": status_val, "health_state": res["status"], "service": "agent-runtime-v2"}


@router.get("/ready", summary="Kubernetes Readiness Probe")
def readiness_probe(response: Response):
    res = default_health_checker.check_all()
    if res["status"] == HealthStatus.NOT_READY.value:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        response.status_code = status.HTTP_200_OK
    return res


@router.get("/live", summary="Kubernetes Liveness Probe")
def liveness_probe():
    return {"status": "alive", "timestamp": time.time()}


@router.get("/deep", summary="Detailed Health & Latency Report")
def deep_health_probe(response: Response):
    res = default_health_checker.check_all()
    if res["status"] == HealthStatus.NOT_READY.value:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return res
