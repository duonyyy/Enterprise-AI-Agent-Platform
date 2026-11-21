"""Token allowance and context admission evaluation."""

from __future__ import annotations

import re
from typing import Any

from agent_contracts.model_contracts import ModelProfile, TokenMeasurement
from agent_runtime.llm.errors import ModelContextTooLargeError


def estimate_tokens_heuristic(text: str) -> int:
    """Conservative token upper-bound heuristic for arbitrary text.

    Approximates ~3.5 chars per token for Latin script plus word-boundary padding,
    ensuring we do not under-estimate required context.
    """
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

    def measure(
        self,
        profile: ModelProfile,
        text: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> TokenMeasurement:
        """Measure tokens with an explicit basis."""
        tokens = estimate_tokens_heuristic(text)
        if tools:
            import json

            tools_str = json.dumps(tools)
            tokens += estimate_tokens_heuristic(tools_str)

        return TokenMeasurement(
            input_tokens=tokens,
            basis="validated_upper_bound",
            profile_revision=profile.schema_version,
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
