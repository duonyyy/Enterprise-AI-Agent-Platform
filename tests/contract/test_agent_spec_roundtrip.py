from pathlib import Path
from agent_contracts.agent_spec import AgentSpec

def test_spec_01_agent_spec_validation():
    root = Path(__file__).resolve().parents[2]
    schema = root / "contracts" / "jsonschema" / "agent-spec.v1.schema.json"
    assert schema.exists()
    spec = AgentSpec.model_validate({
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "quote-assistant"},
        "mode": "fixed_plan",
        "modelRef": "approved-general@2",
        "persona": {"instructions": "Help user with quotes"},
        "limits": {"maxSteps": 10}
    })
    assert spec.metadata.name == "quote-assistant"
