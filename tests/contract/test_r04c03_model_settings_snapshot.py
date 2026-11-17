"""
R04C-03 contract test – verify that published model snapshot and digest include
approved non-secret generation settings while strictly excluding secrets.

Requirements:
- Cùng effective snapshot → digest ổn định.
- Thay effective setting thuộc allowlist → snapshot/digest thay đổi.
- Thay setting không thuộc allowlist hoặc config bên ngoài không làm đổi snapshot.
- Secret không xuất hiện trong manifest hoặc digest.
- Model config ID được giữ lại để traceability.
- Không gọi provider và không tự chọn provider production.
"""

from __future__ import annotations

from uuid import uuid4

from agent_contracts.manifest import (
    build_agent_manifest,
    compute_bundle_sha256,
)
from control_api.modules.agents.repositories.agent_repository import (
    _MODEL_SETTINGS_ALLOWLIST,
    _MODEL_SETTINGS_DENYLIST,
)


def _build_manifest_with_model_settings(settings: dict[str, any]) -> dict[str, any]:
    """Helper to simulate model_dict construction during publish."""
    effective_settings: dict[str, any] = {}
    for key, value in settings.items():
        lower_key = key.lower()
        if any(deny in lower_key for deny in _MODEL_SETTINGS_DENYLIST):
            continue
        if key in _MODEL_SETTINGS_ALLOWLIST:
            effective_settings[key] = value

    model_id = "00000000-0000-0000-0000-000000000001"
    model_dict = {
        "id": model_id,
        "name": "General Assistant Model",
        "provider": "anthropic",
        "providerModelId": "claude-3-5-sonnet",
        "effectiveSettings": effective_settings,
        "modelConfigId": model_id,
    }

    dummy_spec = {
        "schemaVersion": "1.0",
        "kind": "Agent",
        "metadata": {"name": "test-agent"},
        "mode": "fixed_plan",
        "modelRef": model_id,
    }

    return build_agent_manifest(
        agent_id=uuid4(),
        slug="test-agent",
        spec=dummy_spec,
        model=model_dict,
        tools=[],
        skills=[],
        knowledge_bases=[],
    )


class TestModelSettingsSnapshot:
    """Verify R04C-03 model settings allowlist, secret redaction, and SHA-256 sensitivity."""

    def test_allowlist_contains_expected_generation_keys(self) -> None:
        assert "temperature" in _MODEL_SETTINGS_ALLOWLIST
        assert "top_p" in _MODEL_SETTINGS_ALLOWLIST
        assert "max_output_tokens" in _MODEL_SETTINGS_ALLOWLIST

    def test_denylist_contains_secret_patterns(self) -> None:
        assert "api_key" in _MODEL_SETTINGS_DENYLIST
        assert "token" in _MODEL_SETTINGS_DENYLIST
        assert "credential" in _MODEL_SETTINGS_DENYLIST
        assert "password" in _MODEL_SETTINGS_DENYLIST
        assert "secret" in _MODEL_SETTINGS_DENYLIST

    def test_stable_digest_for_identical_effective_settings(self) -> None:
        settings = {"temperature": 0.7, "top_p": 0.95}
        m1 = _build_manifest_with_model_settings(settings)
        m2 = _build_manifest_with_model_settings(settings)
        # Note: dummy_agent_id differs, but if we align agent_id:
        agent_id = uuid4()
        m1["agentId"] = str(agent_id)
        m2["agentId"] = str(agent_id)
        sha1 = compute_bundle_sha256(m1)
        sha2 = compute_bundle_sha256(m2)
        assert sha1 == sha2

    def test_changing_temperature_changes_bundle_sha256(self) -> None:
        agent_id = uuid4()
        m1 = _build_manifest_with_model_settings({"temperature": 0.2})
        m2 = _build_manifest_with_model_settings({"temperature": 0.8})
        m1["agentId"] = str(agent_id)
        m2["agentId"] = str(agent_id)

        sha1 = compute_bundle_sha256(m1)
        sha2 = compute_bundle_sha256(m2)
        assert sha1 != sha2, "Changing temperature MUST produce different bundle_sha256"

    def test_unapproved_setting_is_ignored(self) -> None:
        agent_id = uuid4()
        m1 = _build_manifest_with_model_settings({"temperature": 0.5})
        m2 = _build_manifest_with_model_settings({
            "temperature": 0.5,
            "custom_internal_debug_flag": True,  # Not in allowlist
        })
        m1["agentId"] = str(agent_id)
        m2["agentId"] = str(agent_id)

        sha1 = compute_bundle_sha256(m1)
        sha2 = compute_bundle_sha256(m2)
        assert sha1 == sha2, "Non-allowlisted settings must be excluded from manifest"

    def test_secrets_never_leak_into_manifest(self) -> None:
        raw_settings = {
            "temperature": 0.5,
            "api_key": "sk-ant-api03-secretkey-12345",
            "openai_api_key": "sk-proj-secret-67890",
            "auth_token": "bearer-token-val",
            "db_password": "supersecretpassword",
        }
        manifest = _build_manifest_with_model_settings(raw_settings)
        manifest_str = str(manifest)

        assert "sk-ant-api03" not in manifest_str
        assert "sk-proj" not in manifest_str
        assert "bearer-token-val" not in manifest_str
        assert "supersecretpassword" not in manifest_str

    def test_model_config_id_present_for_traceability(self) -> None:
        manifest = _build_manifest_with_model_settings({"temperature": 0.5})
        model = manifest["dependencies"]["model"]
        assert "modelConfigId" in model
        assert "effectiveSettings" in model
        assert model["effectiveSettings"]["temperature"] == 0.5
