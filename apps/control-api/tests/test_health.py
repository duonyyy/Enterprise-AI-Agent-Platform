from control_api.main import health

def test_control_health():
    response = health()
    assert response["status"] == "ok"
