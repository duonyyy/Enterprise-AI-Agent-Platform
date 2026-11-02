from fastapi.testclient import TestClient
from agent_runtime.main import app

client = TestClient(app)


def test_runtime_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] in ("ok", "ready", "degraded", "not_ready")
