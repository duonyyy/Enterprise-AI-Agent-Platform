from pathlib import Path

def test_openapi_specs():
    root = Path(__file__).resolve().parents[2]
    assert (root / "contracts" / "openapi" / "control-api.openapi.yaml").exists()
    assert (root / "contracts" / "openapi" / "runtime-api.openapi.yaml").exists()
