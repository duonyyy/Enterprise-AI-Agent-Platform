"""Contract checks for the approval decision payload.

Authoritative approval state transitions are covered by the PostgreSQL-backed
CI migration/RLS lane; this suite contains no in-memory approval substitute.
"""

import pytest
from pydantic import ValidationError

from control_api.modules.approvals.schemas import DecisionInputDTO


def test_decision_payload_accepts_only_canonical_decisions():
    payload = DecisionInputDTO(decision="APPROVED", expected_action_hash="a" * 64)
    assert payload.decision == "APPROVED"
    with pytest.raises(ValidationError):
        DecisionInputDTO(decision="approve", expected_action_hash="a" * 64)
