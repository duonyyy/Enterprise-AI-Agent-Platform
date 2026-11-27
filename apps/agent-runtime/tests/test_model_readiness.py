"""R05D-02: readiness uses the startup-owned Gateway without invoking a provider."""

import json
from uuid import uuid4

import pytest
from agent_runtime.api import health
from agent_runtime.main import app
from agent_runtime.settings import RuntimeSettings
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("empty", 503),
        ("unverified", 503),
        ("unpriced", 503),
        ("configured", 200),
        ("redis_down", 503),
    ],
)
def test_actual_http_readiness_uses_provisioned_capabilities(tmp_path, monkeypatch, mode, expected):
    profile = {
        "provider": "fixture",
        "modelId": "fixture-chat",
        "capabilities": {
            "supportsTools": False,
            "supportsJsonSchema": False,
            "supportsStream": True,
            "supportsEmbeddings": False,
        },
        "contextLimits": {"contextWindow": 4096, "maxOutputTokens": 64},
        "pricing": {
            "pricingVersion": "fixture",
            "costPerPromptTokenUsd": "0.00001",
            "costPerCompletionTokenUsd": "0.00002",
        },
    }
    if mode == "unpriced":
        profile.pop("pricing")
    route = {
        "model_config_id": str(uuid4()),
        "adapter": "openai-chat",
        "endpoint_url": "https://approved.example/v1",
        "profile": profile,
        "utf8_bound_verified": mode != "unverified",
    }
    config = tmp_path / "routes.json"
    config.write_text(json.dumps({"routes": [] if mode == "empty" else [route]}), encoding="utf-8")
    settings = RuntimeSettings(_env_file=None, model_gateway_config_path=str(config))
    monkeypatch.setattr("agent_runtime.main.get_settings", lambda: settings)
    monkeypatch.setattr(
        health.default_health_checker,
        "probes",
        {
            name: (
                lambda name=name: not (mode == "redis_down" and name == "semantic_cache_redis"),
                critical,
            )
            for name, (_, critical) in health.default_health_checker.probes.items()
        },
    )

    async def forbid(*_, **__):
        pytest.fail("Health must never perform model inference")

    monkeypatch.setattr(
        "agent_runtime.llm.adapters.http_provider.HttpModelAdapter.generate", forbid
    )
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
        assert response.status_code == expected
        component = next(c for c in response.json()["components"] if c["name"] == "model_gateway")
        assert component["is_critical"] is True
        assert component["capabilities"] == (
            ["generation", "streaming"] if mode in {"configured", "redis_down"} else []
        )
        assert client.get("/health/deep").status_code == expected
    assert app.state.model_gateway is None
