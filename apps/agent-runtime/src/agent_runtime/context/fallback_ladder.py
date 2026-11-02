"""
5-Tier Fallback Ladder (Graceful Degradation).
Guarantees Zero-Crash SLA through tiered resilient fallbacks.
"""

from typing import List, Callable, Any, Dict, Tuple, Optional


class QualityGuardrailFailed(Exception):
    """Raised when output fails quality/faithfulness threshold."""
    pass


class FallbackLadder:
    def __init__(self, tiers: List[Tuple[str, Callable]], validator: Optional[Callable[[Any], bool]] = None):
        """
        tiers: list of (tier_name, callable_func)
        validator: optional callback checking output quality (e.g. faithfulness >= 0.70).
                   If validator returns False, FallbackLadder cascades to the next tier.
        """
        self.tiers = tiers
        self.validator = validator

    def execute_with_fallback(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Executes through tiers sequentially until one succeeds and passes quality gate.
        Never throws unhandled exceptions; returns execution metadata.
        """
        errors = []
        for tier_name, tier_func in self.tiers:
            try:
                result = tier_func(*args, **kwargs)
                if self.validator is not None and not self.validator(result):
                    raise QualityGuardrailFailed(f"Tier '{tier_name}' result failed quality validator")

                return {
                    "success": True,
                    "resolved_tier": tier_name,
                    "result": result,
                    "fallbacks_attempted": errors
                }
            except Exception as e:
                errors.append({"tier": tier_name, "error": str(e)})

        # Ultimate safe degradation
        return {
            "success": False,
            "resolved_tier": "safe_deterministic_degradation",
            "result": "Hệ thống đang bảo trì hoặc chuyển đổi kết nối an toàn. Vui lòng liên hệ quản trị viên.",
            "fallbacks_attempted": errors
        }

