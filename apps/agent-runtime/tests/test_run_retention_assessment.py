"""Clock-boundary policy checks; no object deletion is implemented or simulated."""

from datetime import UTC, datetime, timedelta

import pytest
from agent_runtime.maintenance import _orphan_disposition


@pytest.mark.parametrize(
    ("offset_us", "referenced", "expected"),
    [
        (-1, False, "protected"),
        (0, False, "unknown"),
        (1, False, "unknown"),
        (1, True, "protected"),
    ],
)
def test_orphan_grace_boundary_never_implies_safe_deletion(
    offset_us: int, referenced: bool, expected: str
) -> None:
    modified = datetime(2026, 11, 1, tzinfo=UTC)
    now = modified + timedelta(hours=24, microseconds=offset_us)
    assert _orphan_disposition(now=now, last_modified=modified, referenced=referenced) == expected
