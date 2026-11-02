import hashlib
import json

def publish_agent_revision(workspace_id: str, agent_id: str, actor_id: str) -> dict:
    sample_spec = {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "quotation-assistant"},
        "mode": "fixed_plan",
        "modelRef": "approved-general@2",
        "persona": {"instructions": "Draft quotes"},
        "limits": {"maxSteps": 10}
    }
    spec_str = json.dumps(sample_spec, sort_keys=True)
    spec_hash = hashlib.sha256(spec_str.encode("utf-8")).hexdigest()
    return {
        "agent_id": agent_id,
        "version": 1,
        "spec_hash": spec_hash,
        "is_immutable": True
    }
