"""
R04C-02 contract test – verify that the three resolve methods in
AgentRepository lock the authority / grant / parent rows that
publish_version() actually relies on for its decision.

Category: VERIFIED_SOURCE (static text analysis of Python source).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate the source file reliably (works from repo root or tests/ cwd)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_SRC = (
    _REPO_ROOT
    / "apps"
    / "control-api"
    / "src"
    / "control_api"
    / "modules"
    / "agents"
    / "repositories"
    / "agent_repository.py"
)


@pytest.fixture(scope="module")
def source_text() -> str:
    """Read agent_repository.py as a string."""
    assert _REPO_SRC.exists(), f"source file not found: {_REPO_SRC}"
    return _REPO_SRC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source_ast(source_text: str) -> ast.Module:
    """Parse agent_repository.py into an AST for structural checks."""
    return ast.parse(source_text)


# ── helpers ────────────────────────────────────────────────────────────────

def _extract_method_source(source: str, method_name: str) -> str:
    """Return the raw source text of an async method by name."""
    # Match 'async def <method_name>(' through the next top-level def/class
    pattern = re.compile(
        rf"^(    async def {method_name}\(.*?\n)"  # signature line
        rf"((?:(?!^    (?:async )?def |^class ).+\n)*)",  # body lines
        re.MULTILINE,
    )
    m = pattern.search(source)
    assert m is not None, f"method {method_name} not found in source"
    return m.group(0)


# ── resolve_tool_version: must FOR SHARE lock catalog.tool_grants ──────────

class TestResolveToolVersionGrantLock:
    """resolve_tool_version must acquire FOR SHARE on catalog.tool_grants."""

    def test_contains_tool_grants_for_share(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_tool_version")
        # Must reference the tool_grants table with FOR SHARE
        assert "catalog.tool_grants" in body or "tool_grants" in body, (
            "resolve_tool_version does not reference tool_grants at all"
        )
        assert "FOR SHARE" in body, (
            "resolve_tool_version does not contain any FOR SHARE clause"
        )
        # Specifically, the grant-locking query must mention tool_grants
        # and FOR SHARE in the same SQL string literal sequence.
        assert re.search(
            r"tool_grants.*FOR SHARE", body, re.DOTALL
        ), "resolve_tool_version does not lock tool_grants FOR SHARE"

    def test_grant_lock_is_conditional_on_for_share(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_tool_version")
        # The grant lock must be gated on `for_share`
        assert re.search(r"if\s+for_share\b", body), (
            "resolve_tool_version grant lock is not conditional on for_share"
        )

    def test_grant_lock_wrapped_in_try_except(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_tool_version")
        # The grant-lock block must be wrapped in its own try/except.
        # Look for the pattern: tool_grants ... FOR SHARE ... except SQLAlchemyError
        assert re.search(
            r"tool_grants.*?FOR SHARE.*?except\s+SQLAlchemyError", body, re.DOTALL
        ), (
            "resolve_tool_version grant lock query is not wrapped in "
            "try/except SQLAlchemyError"
        )


# ── resolve_skill_version: must FOR SHARE both sv AND s ────────────────────

class TestResolveSkillVersionParentLock:
    """resolve_skill_version must lock both skill_versions (sv) and skills (s)."""

    def test_lock_clause_includes_sv_and_s(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_skill_version")
        # Accept any whitespace variation of "FOR SHARE OF sv, s"
        assert re.search(
            r"FOR\s+SHARE\s+OF\s+sv\s*,\s*s\b", body
        ), (
            "resolve_skill_version does not contain 'FOR SHARE OF sv, s'; "
            "parent skill row is not locked"
        )

    def test_no_sv_only_lock(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_skill_version")
        # Must NOT have a lock that locks sv alone (old bug)
        matches = re.findall(r'FOR\s+SHARE\s+OF\s+sv\b', body)
        for m in matches:
            # Each match should actually be "FOR SHARE OF sv, s", not just "FOR SHARE OF sv"
            pass
        # The definitive check: there should be no lock clause that locks
        # ONLY sv without s.  We look for "FOR SHARE OF sv" NOT followed by ", s".
        assert not re.search(
            r'FOR\s+SHARE\s+OF\s+sv(?!\s*,\s*s)', body
        ), (
            "resolve_skill_version still has a FOR SHARE OF sv without also locking s"
        )


# ── resolve_knowledge_base: must FOR SHARE lock knowledge.base_grants ──────

class TestResolveKnowledgeBaseGrantLock:
    """resolve_knowledge_base must acquire FOR SHARE on knowledge.base_grants."""

    def test_contains_base_grants_for_share(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_knowledge_base")
        assert "base_grants" in body, (
            "resolve_knowledge_base does not reference base_grants at all"
        )
        # The grant-locking query must reference base_grants and FOR SHARE.
        assert re.search(
            r"base_grants.*FOR SHARE", body, re.DOTALL
        ), "resolve_knowledge_base does not lock base_grants FOR SHARE"

    def test_grant_lock_conditional_on_visibility(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_knowledge_base")
        # The grant lock must NOT fire for workspace-visible bases.
        assert re.search(r"visibility.*!=.*workspace|visibility.*!=.*['\"]workspace['\"]", body), (
            "resolve_knowledge_base grant lock is not gated on non-workspace visibility"
        )

    def test_grant_lock_conditional_on_for_share(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_knowledge_base")
        assert re.search(r"if\s+for_share\b", body), (
            "resolve_knowledge_base grant lock is not conditional on for_share"
        )

    def test_grant_lock_wrapped_in_try_except(self, source_text: str) -> None:
        body = _extract_method_source(source_text, "resolve_knowledge_base")
        # The grant-lock block must be wrapped in its own try/except.
        assert re.search(
            r"base_grants.*?FOR SHARE.*?except\s+SQLAlchemyError", body, re.DOTALL
        ), (
            "resolve_knowledge_base grant lock query is not wrapped in "
            "try/except SQLAlchemyError"
        )
