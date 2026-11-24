"""Contract tests for Model Gateway budget admission, decimal costing, and settlement (G5-06, G5-07, G5-08)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from agent_contracts.manifest import CanonicalizationError
from agent_contracts.model_contracts import (
    ContextLimits,
    ModelCapabilities,
    ModelPricing,
    ModelProfile,
)
from agent_runtime.llm.budget import (
    ReservationSnapshotRates,
    calculate_admission_envelope,
    compute_request_hash,
    compute_settlement_cost,
    compute_settlement_cost_from_snapshot,
    derive_operation_key,
    reserve_usd,
    round_usd,
    validate_transition,
)
from agent_runtime.llm.errors import (
    ModelContextTooLargeError,
    ModelPricingUnavailableError,
    SettlementConflictError,
)


@pytest.fixture
def gpt4o_profile() -> ModelProfile:
    return ModelProfile(
        provider="openai",
        model_id="gpt-4o",
        capabilities=ModelCapabilities(
            supports_tools=True,
            supports_json_schema=True,
            supports_stream=True,
            supports_embeddings=False,
        ),
        context_limits=ContextLimits(
            context_window=128000,
            max_output_tokens=4096,
            output_reserve_margin=1000,
            max_input_tokens=120000,
        ),
        pricing=ModelPricing(
            pricing_version="2026-11-20.1",
            cost_per_prompt_token_usd=Decimal("0.00000250"),
            cost_per_completion_token_usd=Decimal("0.00001000"),
        ),
    )


class TestAdmissionEnvelope:
    def test_calculate_envelope_defaults_to_max_output(self, gpt4o_profile: ModelProfile) -> None:
        envelope = calculate_admission_envelope(gpt4o_profile, prompt_tokens=1500)
        assert envelope.prompt_tokens == 1500
        assert envelope.max_output_tokens == 4096
        assert envelope.worst_case_tokens == 5596
        # prompt cost: 1500 * 0.00000250 = 0.00375000
        # completion cost: 4096 * 0.00001000 = 0.04096000
        # total: 0.04471000
        assert envelope.worst_case_cost_usd == Decimal("0.04471000")
        assert envelope.pricing_version == "2026-11-20.1"
        assert envelope.rate_prompt_token_usd == Decimal("0.00000250")
        assert envelope.rate_completion_token_usd == Decimal("0.00001000")
        assert envelope.pricing_snapshot == {
            "pricing_version": "2026-11-20.1",
            "cost_per_prompt_token_usd": "0.00000250",
            "cost_per_completion_token_usd": "0.00001000",
        }

    def test_calculate_envelope_with_explicit_requested_limit(
        self, gpt4o_profile: ModelProfile
    ) -> None:
        envelope = calculate_admission_envelope(
            gpt4o_profile, prompt_tokens=1000, requested_max_tokens=500
        )
        assert envelope.prompt_tokens == 1000
        assert envelope.max_output_tokens == 500
        assert envelope.worst_case_tokens == 1500
        assert envelope.worst_case_cost_usd == Decimal("0.00750000")

    def test_calculate_envelope_clamps_to_configured_max(
        self, gpt4o_profile: ModelProfile
    ) -> None:
        envelope = calculate_admission_envelope(
            gpt4o_profile, prompt_tokens=1000, requested_max_tokens=99999
        )
        assert envelope.max_output_tokens == 4096

    def test_calculate_envelope_rejects_negative_tokens(
        self, gpt4o_profile: ModelProfile
    ) -> None:
        with pytest.raises(ValueError, match="non-negative integer"):
            calculate_admission_envelope(gpt4o_profile, prompt_tokens=-10)

    def test_calculate_envelope_rejects_context_overflow(
        self, gpt4o_profile: ModelProfile
    ) -> None:
        with pytest.raises(ModelContextTooLargeError, match="exceed model context window"):
            calculate_admission_envelope(gpt4o_profile, prompt_tokens=130000)

        with pytest.raises(ModelContextTooLargeError, match="exceed model context window"):
            calculate_admission_envelope(gpt4o_profile, prompt_tokens=127000)

    def test_unpriced_profile_fails_closed(self) -> None:
        unpriced = ModelProfile(
            provider="openai",
            model_id="gpt-4o",
            capabilities=ModelCapabilities(
                supports_tools=True,
                supports_json_schema=True,
                supports_stream=True,
                supports_embeddings=False,
            ),
            context_limits=ContextLimits(
                context_window=128000,
                max_output_tokens=4096,
            ),
            pricing=None,
        )
        with pytest.raises(ModelPricingUnavailableError, match="monetary admission fails closed"):
            calculate_admission_envelope(unpriced, prompt_tokens=256)

    def test_explicitly_zero_priced_local_model(self) -> None:
        local_e5 = ModelProfile(
            provider="local",
            model_id="e5-small",
            capabilities=ModelCapabilities(
                supports_tools=False,
                supports_json_schema=False,
                supports_stream=False,
                supports_embeddings=True,
            ),
            context_limits=ContextLimits(
                context_window=512,
                max_output_tokens=1,
            ),
            pricing=ModelPricing(
                pricing_version="local-e5-v1",
                cost_per_prompt_token_usd=Decimal("0.00000000"),
                cost_per_completion_token_usd=Decimal("0.00000000"),
            ),
        )
        envelope = calculate_admission_envelope(local_e5, prompt_tokens=256)
        assert envelope.worst_case_tokens == 257
        assert envelope.worst_case_cost_usd == Decimal("0.00000000")
        assert envelope.pricing_version == "local-e5-v1"

    def test_conservative_minimum_priced_reservation_clamping(self) -> None:
        # Micro-priced model: 2 tokens at $0.000000001/token would round down to 0,
        # but conservative admission clamps to at least $0.00000001 (1 microcent)
        micro_profile = ModelProfile(
            provider="custom",
            model_id="micro-llm",
            capabilities=ModelCapabilities(
                supports_tools=False,
                supports_json_schema=False,
                supports_stream=False,
                supports_embeddings=False,
            ),
            context_limits=ContextLimits(context_window=1000, max_output_tokens=1),
            pricing=ModelPricing(
                pricing_version="micro-v1",
                cost_per_prompt_token_usd=Decimal("0.000000001"),
                cost_per_completion_token_usd=Decimal("0.000000001"),
            ),
        )
        envelope = calculate_admission_envelope(micro_profile, prompt_tokens=1)
        assert envelope.worst_case_cost_usd == Decimal("0.00000001")


class TestDecimalSettlementCosting:
    def test_compute_settlement_exact_decimal(self, gpt4o_profile: ModelProfile) -> None:
        cost = compute_settlement_cost(
            gpt4o_profile.pricing,
            input_tokens=1200,
            output_tokens=300,
            usage_source="provider",
        )
        assert cost == Decimal("0.00600000")

    def test_missing_or_unknown_usage_returns_none(self, gpt4o_profile: ModelProfile) -> None:
        assert (
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=None,
                output_tokens=100,
                usage_source="provider",
            )
            is None
        )
        assert (
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=100,
                output_tokens=None,
                usage_source="provider",
            )
            is None
        )
        assert (
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=100,
                output_tokens=100,
                usage_source="unknown",
            )
            is None
        )
        # Unpriced model returns None (cannot confirm monetary cost)
        assert (
            compute_settlement_cost(
                pricing=None,
                input_tokens=100,
                output_tokens=100,
                usage_source="provider",
            )
            is None
        )

    def test_negative_values_strictly_rejected(self, gpt4o_profile: ModelProfile) -> None:
        with pytest.raises(ValueError, match="non-negative integer"):
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=-100,
                output_tokens=1,
                usage_source="provider",
            )

        with pytest.raises(ValueError, match="non-negative integer"):
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=1,
                output_tokens=-5,
                usage_source="provider",
            )

        with pytest.raises(ValueError, match="non-negative integer"):
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=10,
                output_tokens=5,
                usage_source="provider",
                cached_prompt_tokens=-1,
            )

        with pytest.raises(ValueError, match="cannot exceed input_tokens"):
            compute_settlement_cost(
                gpt4o_profile.pricing,
                input_tokens=10,
                output_tokens=5,
                usage_source="provider",
                cached_prompt_tokens=11,
            )

    def test_cache_subset_deduplication(self, gpt4o_profile: ModelProfile) -> None:
        # Prompt: 1000 tokens total, of which 600 were read from prompt cache.
        # Cache read rate: $0.00000125 (50% discount)
        # Regular prompt tokens = 1000 - 600 = 400 tokens @ $0.00000250 = $0.00100000
        # Cached prompt tokens = 600 @ $0.00000125 = $0.00075000
        # Output: 200 tokens @ $0.00001000 = $0.00200000
        # Total = 0.00100000 + 0.00075000 + 0.00200000 = 0.00375000
        cost = compute_settlement_cost(
            gpt4o_profile.pricing,
            input_tokens=1000,
            output_tokens=200,
            usage_source="provider",
            cached_prompt_tokens=600,
            cost_per_cached_token_usd=Decimal("0.00000125"),
        )
        assert cost == Decimal("0.00375000")

    def test_half_up_rounding(self) -> None:
        assert round_usd(Decimal("0.000000015")) == Decimal("0.00000002")
        assert round_usd(Decimal("0.000000014")) == Decimal("0.00000001")

    def test_admission_reservation_rounds_up(self) -> None:
        assert reserve_usd(Decimal("0.000000014")) == Decimal("0.00000002")

    def test_settlement_from_snapshot_rates(self) -> None:
        snapshot = ReservationSnapshotRates(
            pricing_version="2026-11-20.1",
            rate_prompt_token_usd=Decimal("0.00000250"),
            rate_completion_token_usd=Decimal("0.00001000"),
            rate_cached_token_usd=Decimal("0.00000125"),
        )
        cost = compute_settlement_cost_from_snapshot(
            snapshot=snapshot,
            input_tokens=1000,
            output_tokens=200,
            usage_source="provider",
            cached_prompt_tokens=600,
        )
        assert cost == Decimal("0.00375000")


class TestStateMachineTransitions:
    def test_valid_transitions(self) -> None:
        validate_transition("reserved", "dispatched")
        validate_transition("reserved", "released")
        validate_transition("dispatched", "settled")
        validate_transition("dispatched", "unknown")
        validate_transition("unknown", "settled")
        validate_transition("unknown", "released")

    def test_invalid_terminal_transitions(self) -> None:
        with pytest.raises(SettlementConflictError):
            validate_transition("settled", "released")
        with pytest.raises(SettlementConflictError):
            validate_transition("settled", "dispatched")
        with pytest.raises(SettlementConflictError):
            validate_transition("released", "settled")


class TestAntiReplayDeduplication:
    def test_derive_operation_key(self) -> None:
        ws = uuid4()
        caller = uuid4()
        key1 = derive_operation_key(ws, "run", caller, "idemp-123")
        key2 = derive_operation_key(ws, "run", caller, "idemp-123")
        key3 = derive_operation_key(ws, "run", caller, "idemp-456")
        assert key1 == key2
        assert key1 != key3

        other_actor = derive_operation_key(ws, "run", uuid4(), "idemp-123")
        assert other_actor == key1

    def test_derive_operation_key_rejects_invalid_kind(self) -> None:
        ws = uuid4()
        caller = uuid4()
        with pytest.raises(ValueError, match="Invalid caller_kind"):
            derive_operation_key(ws, "invalid_kind", caller, "idemp-123")

    def test_compute_request_hash_stability_rfc8785(self) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        actor_id = uuid4()
        model_id = uuid4()
        msgs = [{"role": "user", "content": "hello"}]

        h1 = compute_request_hash(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            model_config_id=model_id,
            requested_provider="openai",
            requested_model="gpt-4o",
            messages=msgs,
            settings={"temperature": 0.5},
        )
        h2 = compute_request_hash(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            model_config_id=model_id,
            requested_provider="openai",
            requested_model="gpt-4o",
            messages=msgs,
            settings={"temperature": 0.5},
        )
        # Temperature change produces different hash
        h3 = compute_request_hash(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            model_config_id=model_id,
            requested_provider="openai",
            requested_model="gpt-4o",
            messages=msgs,
            settings={"temperature": 0.7},
        )
        assert h1 == h2
        assert h1 != h3

    def test_compute_request_hash_rejects_nan(self) -> None:
        tenant_id = uuid4()
        workspace_id = uuid4()
        actor_id = uuid4()
        model_id = uuid4()
        msgs = [{"role": "user", "content": "hello"}]

        with pytest.raises(CanonicalizationError):
            compute_request_hash(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                model_config_id=model_id,
                requested_provider="openai",
                requested_model="gpt-4o",
                messages=msgs,
                settings={"temperature": float("nan")},
            )
