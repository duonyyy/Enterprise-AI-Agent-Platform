"""Contract test vectors for RFC 8785 JSON Canonicalization Scheme (JCS) and manifest hashing.

Authority:
- RFC 8785: JSON Canonicalization Scheme.
- ADR-009: Immutable publishing with bundle SHA-256.
- AG-IMPL-001 Section 5: JCS key order, Unicode UTF-16, numbers, -0, non-finite, duplicate keys rejection.
- TEST-AG-001 Scenario C13.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from agent_contracts.manifest import (
    CanonicalizationError,
    build_agent_manifest,
    canonicalize_json,
    compute_bundle_sha256,
    compute_request_sha256,
)


def test_jcs_key_sorting_utf16_code_units():
    """Verify keys are sorted by UTF-16 code unit values per RFC 8785 section 3.2.3."""
    data = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\n": "Newline",
        "1": "One",
        "\u0080": "Control",
        "\u007f": "Delete",
        "a": "Alpha",
        "\U0001f600": "Grinning Face",
    }
    canonical = canonicalize_json(data)
    # RFC 8785 sorts strictly by UTF-16 code units
    parsed_keys = list(json.loads(canonical.decode("utf-8")).keys())
    # \n (0x0A) < \r (0x0D) < "1" (0x31) < "a" (0x61) < \u007f (0x7F) < \u0080 (0x80) < \u20ac (0x20AC) < \U0001F600 (0xD83D 0xDE00)
    expected_order = [
        "\n",
        "\r",
        "1",
        "a",
        "\u007f",
        "\u0080",
        "\u20ac",
        "\U0001f600",
    ]
    assert parsed_keys == expected_order


def test_jcs_number_formatting_and_zero_normalization():
    """Verify RFC 8785 number formatting rules: integer representations, -0 to 0, exponential bounds."""
    cases = [
        ({"num": 0}, b'{"num":0}'),
        ({"num": -0.0}, b'{"num":0}'),
        ({"num": 1.0}, b'{"num":1}'),
        ({"num": 100.0}, b'{"num":100}'),
        ({"num": 100000000000000000000.0}, b'{"num":100000000000000000000}'),
        ({"num": 0.000001}, b'{"num":0.000001}'),
        ({"num": 1e-7}, b'{"num":1e-7}'),
        ({"num": 3.141592653589793}, b'{"num":3.141592653589793}'),
    ]
    for val, expected in cases:
        assert canonicalize_json(val) == expected, f"Failed for {val}"


def test_jcs_whitespace_and_string_escapes():
    """Verify no extraneous whitespace and minimal RFC 8785 string escaping."""
    data = {"hello": "world", "special": 'quote: " backslash: \\ slash: / tab: \t'}
    canonical = canonicalize_json(data)
    # Slash / must NOT be escaped in RFC 8785 (\/), tabs are \t
    assert b"\\/" not in canonical
    assert b"\\t" in canonical
    assert (
        b'{"hello":"world","special":"quote: \\" backslash: \\\\ slash: / tab: \\t"}' == canonical
    )


def test_jcs_array_order_preservation():
    """Verify array order is strictly preserved and not sorted."""
    data = {"tools": ["tool-z", "tool-a", "tool-m"]}
    canonical = canonicalize_json(data)
    assert canonical == b'{"tools":["tool-z","tool-a","tool-m"]}'


def test_jcs_rejects_non_finite_numbers():
    """Verify NaN, Infinity, -Infinity are rejected with CanonicalizationError."""
    with pytest.raises(CanonicalizationError):
        canonicalize_json({"val": float("nan")})

    with pytest.raises(CanonicalizationError):
        canonicalize_json({"val": float("inf")})

    with pytest.raises(CanonicalizationError):
        canonicalize_json({"val": float("-inf")})


def test_compute_bundle_sha256_deterministic_content_addressing():
    """Verify two manifests with identical content in different key orders produce identical hashes."""
    agent_id = uuid4()
    m1 = build_agent_manifest(
        agent_id=agent_id,
        slug="test-agent",
        spec={"schemaVersion": "1.0", "kind": "Agent", "b": 2, "a": 1},
        model={"provider": "vinai", "name": "llama"},
        tools=[{"toolId": "t1", "version": 1}],
    )
    m2 = build_agent_manifest(
        agent_id=agent_id,
        slug="test-agent",
        spec={"kind": "Agent", "schemaVersion": "1.0", "a": 1, "b": 2},
        tools=[{"version": 1, "toolId": "t1"}],
        model={"name": "llama", "provider": "vinai"},
    )
    hash1 = compute_bundle_sha256(m1)
    hash2 = compute_bundle_sha256(m2)
    assert hash1 == hash2
    assert len(hash1) == 64
    assert all(c in "0123456789abcdef" for c in hash1)

    # Any change in content produces a different hash
    m3 = build_agent_manifest(
        agent_id=agent_id,
        slug="test-agent",
        spec={"schemaVersion": "1.0", "kind": "Agent", "b": 3, "a": 1},
        model={"provider": "vinai", "name": "llama"},
        tools=[{"toolId": "t1", "version": 1}],
    )
    assert compute_bundle_sha256(m3) != hash1


def test_compute_request_sha256_binding():
    """Verify request digest binds scope, actor, expected_revision, and operation deterministically."""
    tenant_id = uuid4()
    workspace_id = uuid4()
    agent_id = uuid4()
    actor_id = uuid4()

    h1 = compute_request_sha256(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_user_id=actor_id,
        expected_revision=1,
    )
    h2 = compute_request_sha256(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_user_id=actor_id,
        expected_revision=1,
    )
    assert h1 == h2

    # Different actor -> different request hash
    h_diff_actor = compute_request_sha256(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_user_id=uuid4(),
        expected_revision=1,
    )
    assert h1 != h_diff_actor

    # Different revision -> different request hash
    h_diff_rev = compute_request_sha256(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        actor_user_id=actor_id,
        expected_revision=2,
    )
    assert h1 != h_diff_rev
