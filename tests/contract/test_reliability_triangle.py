"""
Unit and Contract Test for Runtime Reliability Triangle (Track 2 Day 25).
Covers:
1. CircuitBreaker 3 states (CLOSED, OPEN, HALF_OPEN) + Full Jitter backoff.
2. SemanticVectorCache cosine similarity lookup, TTL, and cache hit.
3. FallbackLadder 5-tier graceful degradation + QualityGuardrail validation.
"""

import time
import pytest
from agent_runtime.context.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
)
from agent_runtime.context.semantic_cache import SemanticVectorCache
from agent_runtime.context.fallback_ladder import FallbackLadder, QualityGuardrailFailed


def test_circuit_breaker_transitions():
    cb = CircuitBreaker(failure_threshold=2, recovery_time_seconds=0.1, half_open_success_threshold=1)

    assert cb.state == CircuitState.CLOSED

    def failing_call():
        raise ConnectionError("GPU node down")

    # First failure
    with pytest.raises(ConnectionError):
        cb.call(failing_call)
    assert cb.state == CircuitState.CLOSED

    # Second failure -> triggers OPEN
    with pytest.raises(ConnectionError):
        cb.call(failing_call)
    assert cb.state == CircuitState.OPEN

    # While OPEN -> fast-fails immediately with CircuitBreakerOpenException
    with pytest.raises(CircuitBreakerOpenException):
        cb.call(failing_call)

    # Wait for recovery delay
    time.sleep(0.12)

    # Next call transitions to HALF_OPEN
    def successful_call():
        return "success"

    res = cb.call(successful_call)
    assert res == "success"
    # Successful probe resets to CLOSED
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_full_jitter():
    cb = CircuitBreaker()
    for attempt in range(4):
        delay = cb.compute_backoff(attempt=attempt, base_delay=0.5, max_delay=4.0)
        max_possible = min(4.0, 0.5 * (2 ** attempt))
        assert 0.0 <= delay <= max_possible


def test_semantic_vector_cache_hit_and_ttl():
    cache = SemanticVectorCache(similarity_threshold=0.80, ttl_seconds=0.5)

    cache.store("Làm thế nào để tạo Agent mới trong hệ thống?", "Vào trang Agent Studio và bấm Create Agent.")

    # High semantic similarity query (synonym variation: 'Làm sao' vs 'Làm thế nào')
    ans, score, latency_ms = cache.lookup("Làm sao để tạo Agent mới trong hệ thống?")
    assert ans == "Vào trang Agent Studio và bấm Create Agent."
    assert score >= 0.80
    assert latency_ms < 50.0  # Sub-50ms deterministic in-memory calculation

    # Unrelated query -> cache miss
    ans_miss, score_miss, _ = cache.lookup("Công thức tính diện tích hình tròn?")
    assert ans_miss is None
    assert score_miss < 0.50

    # Wait for TTL expiry
    time.sleep(0.6)
    ans_expired, _, _ = cache.lookup("Làm sao để tạo Agent mới trong hệ thống?")
    assert ans_expired is None


def test_fallback_ladder_with_quality_guardrail():
    # Tier 1 fails with exception
    def tier1_vllm_primary():
        raise TimeoutError("vLLM primary timeout")

    # Tier 2 returns low quality / hallucinatory answer
    def tier2_small_model():
        return {"answer": "Tôi không biết.", "faithfulness": 0.45}

    # Tier 3 succeeds with high quality
    def tier3_cloud_api():
        return {"answer": "Hướng dẫn đầy đủ cấu hình RAG.", "faithfulness": 0.95}

    def quality_validator(res):
        return isinstance(res, dict) and res.get("faithfulness", 0.0) >= 0.70

    ladder = FallbackLadder(
        tiers=[
            ("tier1_vllm_primary", tier1_vllm_primary),
            ("tier2_small_model", tier2_small_model),
            ("tier3_cloud_api", tier3_cloud_api),
        ],
        validator=quality_validator,
    )

    execution = ladder.execute_with_fallback()
    assert execution["success"] is True
    assert execution["resolved_tier"] == "tier3_cloud_api"
    assert execution["result"]["faithfulness"] == 0.95
    assert len(execution["fallbacks_attempted"]) == 2
