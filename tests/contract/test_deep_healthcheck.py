"""
Unit and Contract Test for Deep Healthcheck (Track 2 Day 28 IP08 & Day 23 DR).
Verifies 3-state health logic (ready, degraded, not_ready), timeout protection
against frozen sockets / thread hangs (SIGSTOP simulation), and FastAPI router endpoints.
"""

import time
import pytest
from fastapi import Response

from agent_runtime.api.health import HealthChecker, HealthStatus, deep_health_probe, get_health, liveness_probe


def test_health_checker_all_healthy():
    checker = HealthChecker(probe_timeout_seconds=0.2)
    checker.register_probe("db", lambda: True, is_critical=True)
    checker.register_probe("cache", lambda: True, is_critical=False)

    report = checker.check_all()
    assert report["status"] == HealthStatus.READY.value
    assert len(report["components"]) == 2
    assert report["components"][0]["status"] == "ok"
    assert report["components"][1]["status"] == "ok"


def test_health_checker_degraded_when_non_critical_fails():
    checker = HealthChecker(probe_timeout_seconds=0.2)
    checker.register_probe("db", lambda: True, is_critical=True)
    checker.register_probe("optional_redis_cache", lambda: False, is_critical=False)

    report = checker.check_all()
    assert report["status"] == HealthStatus.DEGRADED.value
    comp_map = {c["name"]: c for c in report["components"]}
    assert comp_map["db"]["status"] == "ok"
    assert comp_map["optional_redis_cache"]["status"] == "degraded"


def test_health_checker_not_ready_when_critical_fails():
    checker = HealthChecker(probe_timeout_seconds=0.2)
    checker.register_probe("primary_postgres", lambda: False, is_critical=True)
    checker.register_probe("cache", lambda: True, is_critical=False)

    report = checker.check_all()
    assert report["status"] == HealthStatus.NOT_READY.value
    comp_map = {c["name"]: c for c in report["components"]}
    assert comp_map["primary_postgres"]["status"] == "failed"


def test_health_checker_sigstop_hang_protection():
    """Simulates a frozen socket or process (SIGSTOP) that never returns."""
    checker = HealthChecker(probe_timeout_seconds=0.1)

    def hanging_probe():
        time.sleep(2.0)  # Hang longer than probe_timeout_seconds
        return True

    checker.register_probe("frozen_downstream_service", hanging_probe, is_critical=True)

    start = time.perf_counter()
    report = checker.check_all()
    elapsed = time.perf_counter() - start

    # Must timeout quickly around 0.1s, NOT hang for 2.0s
    assert elapsed < 0.5
    assert report["status"] == HealthStatus.NOT_READY.value
    comp = report["components"][0]
    assert comp["status"] == "failed"
    assert "timed out" in comp["message"].lower()


def test_fastapi_health_endpoints():
    health = get_health()
    assert health["status"] in ("ok", "ready", "degraded", "not_ready")

    liveness = liveness_probe()
    assert liveness["status"] == "alive"

    response = Response()
    data = deep_health_probe(response)
    assert response.status_code in (200, 503)
    assert "components" in data
    assert len(data["components"]) >= 2
