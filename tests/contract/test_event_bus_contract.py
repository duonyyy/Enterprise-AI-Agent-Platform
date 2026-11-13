"""Configuration guards for durable event publication."""

import pytest
from cryptography.fernet import Fernet
from platform_common.event_bus import (
    EventBusConfigurationError,
    PostgresOutboxEventBus,
    get_event_bus,
)
from platform_common.invitations import (
    InvitationCryptoError,
    InvitationEmailError,
    InvitationProofBinding,
    InvitationProofProtector,
    build_invitation_accept_url,
    normalize_invitation_email,
)


def test_postgres_outbox_requires_session_factory():
    with pytest.raises(EventBusConfigurationError):
        PostgresOutboxEventBus(None)


def test_runtime_factory_rejects_in_memory_mode(monkeypatch):
    monkeypatch.setenv("EVENT_BUS_TYPE", "in_memory")
    with pytest.raises(EventBusConfigurationError):
        get_event_bus()


def test_invitation_email_normalization_strict_rules():
    assert normalize_invitation_email("  Alice.Smith+Team@Example.COM  ") == "alice.smith+team@example.com"
    for invalid in (
        "Alice <alice@example.com>",
        "alice\r\n@example.com",
        "alice\n@example.com",
        "alice@example",
        "alice smith@example.com",
        "äöü@example.com",
        "a" * 65 + "@example.com",
    ):
        with pytest.raises(InvitationEmailError):
            normalize_invitation_email(invalid)


def test_invitation_proof_protector_binding_and_fail_closed():
    key1 = Fernet.generate_key().decode("ascii")
    key2 = Fernet.generate_key().decode("ascii")
    protector1 = InvitationProofProtector(key1, key_id="v1")
    protector2 = InvitationProofProtector(key2, key_id="v1")

    binding = InvitationProofBinding(
        invitation_id="00000000-0000-0000-0000-000000000101",
        tenant_id="00000000-0000-0000-0000-000000000001",
        workspace_id="00000000-0000-0000-0000-000000000010",
        generation=1,
        recipient_issuer="http://localhost:8080/realms/enterprise",
        recipient_email_normalized="alice.smith+tag@example.com",
        role="developer",
    )
    proof, token_hash = protector1.mint_proof()
    ciphertext, key_id = protector1.encrypt_proof(proof, binding)
    assert key_id == "v1"
    assert (
        protector1.decrypt_and_verify_proof(
            ciphertext,
            key_id,
            binding,
            expected_token_hash=token_hash,
        )
        == proof
    )

    url = build_invitation_accept_url("https://studio.example.com/", binding.invitation_id, proof)
    assert url == f"https://studio.example.com/invitations/{binding.invitation_id}/accept#token={proof}"

    with pytest.raises(InvitationCryptoError):
        protector2.decrypt_and_verify_proof(ciphertext, key_id, binding, expected_token_hash=token_hash)

    with pytest.raises(InvitationCryptoError):
        protector1.decrypt_and_verify_proof(ciphertext, "v2", binding, expected_token_hash=token_hash)

    with pytest.raises(InvitationCryptoError):
        protector1.decrypt_and_verify_proof(
            ciphertext,
            key_id,
            InvitationProofBinding(
                invitation_id=binding.invitation_id,
                tenant_id=binding.tenant_id,
                workspace_id=binding.workspace_id,
                generation=2,
                recipient_issuer=binding.recipient_issuer,
                recipient_email_normalized=binding.recipient_email_normalized,
                role=binding.role,
            ),
            expected_token_hash=token_hash,
        )

    with pytest.raises(InvitationCryptoError):
        protector1.decrypt_and_verify_proof(
            ciphertext,
            key_id,
            binding,
            expected_token_hash="0" * 64,
        )

    with pytest.raises(InvitationCryptoError):
        InvitationProofProtector("not-a-fernet-key")
