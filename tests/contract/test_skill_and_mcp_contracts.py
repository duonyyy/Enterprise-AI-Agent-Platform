import pytest
from pathlib import Path
from agent_contracts.agent_spec import AgentSpec
from agent_contracts.skill_spec import SkillManifest, SkillRef
from agent_contracts.mcp_spec import McpConnectionConfig, McpDiscoverySnapshot
from agent_runtime.skills.resource_reader import SkillResourceReader, SkillResourceSecurityViolation
from agent_runtime.mcp.client import McpClientAdapter
from agent_runtime.mcp.errors import McpEndpointBlockedError

def test_skill_manifest_validation():
    manifest = SkillManifest.model_validate({
        "schemaVersion": "1.0",
        "name": "weekly-report",
        "description": "Generate structured weekly progress reports.",
        "resources": ["references/report-template.md"]
    })
    assert manifest.name == "weekly-report"
    assert manifest.resources == ["references/report-template.md"]

def test_agent_spec_with_skill_refs():
    spec = AgentSpec.model_validate({
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "ops-agent"},
        "mode": "fixed_plan",
        "modelRef": "approved-general@2",
        "persona": {"instructions": "Perform operations"},
        "skillRefs": [
            {"skillId": "skill-1", "skillVersionId": "skill-ver-1"}
        ],
        "limits": {"maxSteps": 5}
    })
    assert len(spec.skillRefs) == 1
    assert spec.skillRefs[0].skillId == "skill-1"

def test_mcp_connection_config():
    config = McpConnectionConfig.model_validate({
        "schemaVersion": "1.0",
        "transport": "streamable_http",
        "endpoint": "https://mcp.internal.example.com/mcp",
        "authMode": "service_token",
        "secretRef": "vault://credentials/mcp-token"
    })
    assert config.transport == "streamable_http"
    assert config.connectTimeoutMs == 5000

def test_skill_resource_reader_security():
    reader = SkillResourceReader(resource_store={
        "ver-1": {"references/template.md": "# Template"}
    })
    # Valid access
    content = reader.read_resource("ver-1", "references/template.md")
    assert content == "# Template"

    # Traversal attack blocked
    with pytest.raises(SkillResourceSecurityViolation):
        reader.read_resource("ver-1", "../../etc/passwd")

    # Scripts execution blocked in MVP
    with pytest.raises(PermissionError):
        reader.read_resource("ver-1", "scripts/run.sh")

def test_mcp_client_ssrf_egress_security():
    adapter = McpClientAdapter(
        allow_localhost_dev=False,
        dns_resolver=lambda host, port: ["93.184.216.34"],
    )

    # Allowed valid external HTTPS
    assert adapter.validate_endpoint("https://mcp.external-partner.com/mcp") is True

    # Blocked local IP
    with pytest.raises(McpEndpointBlockedError):
        adapter.validate_endpoint("http://127.0.0.1:8000/mcp")

    # Blocked cloud metadata IP
    with pytest.raises(McpEndpointBlockedError):
        adapter.validate_endpoint("http://169.254.169.254/latest/meta-data")

    # Blocked 172.31.0.1 (RFC1918 172.16.0.0/12)
    with pytest.raises(McpEndpointBlockedError):
        adapter.validate_endpoint("http://172.31.0.1:8080/mcp")

    # Blocked integer loopback 2130706433 (127.0.0.1)
    with pytest.raises(McpEndpointBlockedError):
        adapter.validate_endpoint("http://2130706433/mcp")

    # Blocked IPv6 loopback [::1]
    with pytest.raises(McpEndpointBlockedError):
        adapter.validate_endpoint("http://[::1]:8080/mcp")

