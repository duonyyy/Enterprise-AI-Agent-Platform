"""Token allowance and context admission evaluation."""

from __future__ import annotations

import re
from typing import Any

from agent_contracts.model_contracts import ModelProfile, TokenMeasurement

from agent_runtime.llm.errors import ModelContextTooLargeError


def estimate_tokens_heuristic(text: str) -> int:
    """Legacy character estimate, never sufficient to admit a hard token budget."""
    if not text:
        return 0
    words = len(re.findall(r"\w+", text))
    chars = len(text)
    # Estimate roughly 1 token per 3.5 chars + 1 token per word boundary
    bound = int(math_ceil(chars / 3.5)) + (words // 10)
    return max(1, bound)


def math_ceil(v: float) -> int:
    import math

    return math.ceil(v)


class TokenProfiler:
    """Local token profiler providing token measurement and admission evaluation."""

    def __init__(self, *, validated_utf8_models: frozenset[tuple[str, str]] = frozenset()) -> None:
        # Only enable after checking the selected provider tokenizer's byte-level
        # upper bound and framing. Unknown vendor tokenizers must fail admission.
        self._validated_utf8_models = validated_utf8_models

    def measure(
        self,
        profile: ModelProfile,
        text: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> TokenMeasurement:
        """Measure tokens with an explicit basis."""
        verified = (profile.provider, profile.model_id) in self._validated_utf8_models
        tokens = len(text.encode("utf-8")) + 256 if verified else estimate_tokens_heuristic(text)
        if tools:
            import json

            tools_str = json.dumps(tools)
            tokens += (
                len(tools_str.encode("utf-8")) + 64 * len(tools)
                if verified
                else estimate_tokens_heuristic(tools_str)
            )

        return TokenMeasurement(
            inputTokens=tokens,
            basis="validated_upper_bound" if verified else "unknown",
            profileRevision=profile.schema_version,
        )

    def evaluate_admission(
        self,
        profile: ModelProfile,
        required_input_tokens: int,
        output_reserve: int | None = None,
        safety_margin: int | None = None,
    ) -> int:
        """Verify context budget admission.

        Returns allowable input tokens if admission passes,
        or raises ModelContextTooLargeError.
        """
        allowance = profile.calculate_allowance(
            output_reserve=output_reserve,
            safety_margin=safety_margin,
        )
        if allowance <= 0:
            raise ModelContextTooLargeError(
                f"Model profile '{profile.model_id}' has non-positive input allowance ({allowance})"
            )
        if required_input_tokens > allowance:
            raise ModelContextTooLargeError(
                f"Required input tokens ({required_input_tokens}) exceeds context allowance ({allowance}) "
                f"for model '{profile.model_id}'"
            )
        return allowance
