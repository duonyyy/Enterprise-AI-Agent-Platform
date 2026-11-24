"""Durable model budget, admission envelope, and settlement accounting logic according to SPEC-LLM-001."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from agent_contracts.manifest import canonicalize_json
from agent_contracts.model_contracts import ModelPricing, ModelProfile, UsageSource

from agent_runtime.llm.errors import (
    ModelContextTooLargeError,
    ModelPricingUnavailableError,
    SettlementConflictError,
)

ReservationState = Literal["reserved", "dispatched", "settled", "released", "unknown"]
CallerKind = Literal["user", "service_account", "run", "job", "eval", "standalone"]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "reserved": {"dispatched", "released"},
    "dispatched": {"settled", "unknown"},
    "settled": set(),  # Terminal state
    "released": set(),  # Terminal state
    "unknown": {"settled", "released"},  # Reconcilable by authorized worker
}

DECIMAL_PLACES_8 = Decimal("0.00000001")
MINIMUM_PRICED_RESERVATION_COST = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class AdmissionEnvelope:
    """Calculated worst-case token and cost envelope for pre-dispatch admission."""

    prompt_tokens: int
    max_output_tokens: int
    worst_case_tokens: int
    worst_case_cost_usd: Decimal
    pricing_version: str
    rate_prompt_token_usd: Decimal
    rate_completion_token_usd: Decimal
    pricing_snapshot: dict[str, str]


@dataclass(frozen=True, slots=True)
class ReservationSnapshotRates:
    """Immutable pricing rates captured at reservation time to guarantee deterministic settlement."""

    pricing_version: str
    rate_prompt_token_usd: Decimal
    rate_completion_token_usd: Decimal
    rate_cached_token_usd: Decimal | None = None


def round_usd(amount: Decimal) -> Decimal:
    """Round USD amounts using standard Decimal ROUND_HALF_UP to 8 decimal places."""
    if not amount.is_finite() or amount < 0:
        raise ValueError("USD amount must be finite and non-negative")
    return amount.quantize(DECIMAL_PLACES_8, rounding=ROUND_HALF_UP)


def reserve_usd(amount: Decimal) -> Decimal:
    """Round a hard admission reservation upward to avoid under-reserving budget."""
    if not amount.is_finite() or amount < 0:
        raise ValueError("USD amount must be finite and non-negative")
    return amount.quantize(DECIMAL_PLACES_8, rounding=ROUND_CEILING)


def calculate_admission_envelope(
    profile: ModelProfile,
    prompt_tokens: int,
    requested_max_tokens: int | None = None,
) -> AdmissionEnvelope:
    """Calculate worst-case token envelope and monetary reservation cost.

    Formula:
    max_out = min(requested_max_tokens, profile.context_limits.max_output_tokens)
    worst_case_tokens = prompt_tokens + max_out
    worst_case_cost = (prompt_tokens * cost_per_prompt) + (max_out * cost_per_completion)

    Fails closed with ModelPricingUnavailableError when authoritative pricing is missing,
    unless profile is explicitly zero-priced.
    """
    if type(prompt_tokens) is not int or prompt_tokens < 0:
        raise ValueError("prompt_tokens must be a non-negative integer")

    context_window = profile.context_limits.context_window
    if prompt_tokens > context_window:
        raise ModelContextTooLargeError(
            f"Prompt tokens ({prompt_tokens}) exceed model context window ({context_window})"
        )

    configured_max_out = profile.context_limits.max_output_tokens
    if requested_max_tokens is not None and requested_max_tokens > 0:
        max_output_tokens = min(requested_max_tokens, configured_max_out)
    else:
        max_output_tokens = configured_max_out

    worst_case_tokens = prompt_tokens + max_output_tokens
    if worst_case_tokens > context_window:
        raise ModelContextTooLargeError(
            f"Worst-case tokens ({worst_case_tokens}) exceed model context window ({context_window})"
        )

    if profile.pricing is None:
        raise ModelPricingUnavailableError(
            f"Model profile '{profile.model_id}' lacks authoritative pricing; monetary admission fails closed."
        )

    cost_prompt = profile.pricing.cost_per_prompt_token_usd
    cost_comp = profile.pricing.cost_per_completion_token_usd
    if (
        not cost_prompt.is_finite()
        or not cost_comp.is_finite()
        or cost_prompt < 0
        or cost_comp < 0
    ):
        raise ValueError("Pricing rates must be non-negative")

    raw_cost = (Decimal(prompt_tokens) * cost_prompt) + (Decimal(max_output_tokens) * cost_comp)
    total_cost = reserve_usd(raw_cost)

    # Conservative admission: if model has non-zero rates and worst_case_tokens > 0,
    # clamp minimum reservation to at least 1 unit of precision (1 microcent = 0.00000001 USD)
    if (cost_prompt > 0 or cost_comp > 0) and worst_case_tokens > 0:
        total_cost = max(total_cost, MINIMUM_PRICED_RESERVATION_COST)

    return AdmissionEnvelope(
        prompt_tokens=prompt_tokens,
        max_output_tokens=max_output_tokens,
        worst_case_tokens=worst_case_tokens,
        worst_case_cost_usd=total_cost,
        pricing_version=profile.pricing.pricing_version,
        rate_prompt_token_usd=cost_prompt,
        rate_completion_token_usd=cost_comp,
        pricing_snapshot={
            "pricing_version": profile.pricing.pricing_version,
            "cost_per_prompt_token_usd": str(cost_prompt),
            "cost_per_completion_token_usd": str(cost_comp),
        },
    )


def compute_settlement_cost(
    pricing: ModelPricing | None,
    input_tokens: int | None,
    output_tokens: int | None,
    usage_source: UsageSource,
    cached_prompt_tokens: int | None = None,
    cost_per_cached_token_usd: Decimal | None = None,
) -> Decimal | None:
    """Compute verified settlement cost in USD using exact Decimal arithmetic.

    - If usage_source is 'unknown' or tokens are None: returns None (never fake 0!).
    - If pricing is None: returns None (unpriced model cannot confirm monetary cost).
    - Cache subset semantics: cached_prompt_tokens is a strict subset of input_tokens.
      Non-cached prompt tokens = input_tokens - cached_prompt_tokens.
      Cached tokens are charged at cost_per_cached_token_usd (if defined).
    - Rejects negative tokens, negative prices, or cached_prompt_tokens > input_tokens.
    """
    if usage_source == "unknown":
        return None

    if input_tokens is None or output_tokens is None:
        return None

    if type(input_tokens) is not int or input_tokens < 0:
        raise ValueError("input_tokens must be a non-negative integer")
    if type(output_tokens) is not int or output_tokens < 0:
        raise ValueError("output_tokens must be a non-negative integer")

    if cached_prompt_tokens is not None:
        if type(cached_prompt_tokens) is not int or cached_prompt_tokens < 0:
            raise ValueError("cached_prompt_tokens must be a non-negative integer")
        if cached_prompt_tokens > input_tokens:
            raise ValueError(
                f"cached_prompt_tokens ({cached_prompt_tokens}) cannot exceed input_tokens ({input_tokens})"
            )

    if pricing is None:
        return None

    if (
        not pricing.cost_per_prompt_token_usd.is_finite()
        or not pricing.cost_per_completion_token_usd.is_finite()
        or pricing.cost_per_prompt_token_usd < 0
        or pricing.cost_per_completion_token_usd < 0
    ):
        raise ValueError("Pricing rates must be non-negative")
    if (
        cost_per_cached_token_usd is not None
        and (not cost_per_cached_token_usd.is_finite() or cost_per_cached_token_usd < 0)
    ):
        raise ValueError("Cached token price rate must be non-negative")

    in_tokens = Decimal(input_tokens)
    out_tokens = Decimal(output_tokens)

    if cached_prompt_tokens is not None and cached_prompt_tokens > 0:
        cached_count = Decimal(cached_prompt_tokens)
        regular_in = in_tokens - cached_count
        cache_rate = (
            cost_per_cached_token_usd
            if cost_per_cached_token_usd is not None
            else pricing.cost_per_prompt_token_usd
        )
        prompt_cost = (regular_in * pricing.cost_per_prompt_token_usd) + (cached_count * cache_rate)
    else:
        prompt_cost = in_tokens * pricing.cost_per_prompt_token_usd

    completion_cost = out_tokens * pricing.cost_per_completion_token_usd
    return round_usd(prompt_cost + completion_cost)


def compute_settlement_cost_from_snapshot(
    snapshot: ReservationSnapshotRates,
    input_tokens: int | None,
    output_tokens: int | None,
    usage_source: UsageSource,
    cached_prompt_tokens: int | None = None,
) -> Decimal | None:
    """Compute verified settlement cost using immutable rates locked at reservation time."""
    pricing = ModelPricing(
        pricing_version=snapshot.pricing_version,
        cost_per_prompt_token_usd=snapshot.rate_prompt_token_usd,
        cost_per_completion_token_usd=snapshot.rate_completion_token_usd,
    )
    return compute_settlement_cost(
        pricing=pricing,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_source=usage_source,
        cached_prompt_tokens=cached_prompt_tokens,
        cost_per_cached_token_usd=snapshot.rate_cached_token_usd,
    )


def validate_transition(current_state: str, new_state: str) -> None:
    """Validate reservation state machine transition, rejecting invalid or terminal moves."""
    allowed = VALID_TRANSITIONS.get(current_state)
    if allowed is None:
        raise SettlementConflictError(f"Unknown reservation state: {current_state}")
    if new_state not in allowed:
        raise SettlementConflictError(
            f"Cannot transition reservation from '{current_state}' to '{new_state}'"
        )


def derive_operation_key(
    workspace_id: UUID | str,
    caller_kind: str,
    caller_id: UUID | str,
    idempotency_key: str,
) -> str:
    """Normalize a client idempotency key for a workspace-scoped reservation.

    Database uniqueness supplies the trusted workspace boundary.  Caller identity
    is stored and compared separately, so the same key cannot be replayed by a
    different caller to obtain another caller's reservation receipt.
    """
    valid_kinds = {"user", "service_account", "run", "job", "eval", "standalone"}
    if caller_kind not in valid_kinds:
        raise ValueError(f"Invalid caller_kind '{caller_kind}'. Must be one of {valid_kinds}")
    del workspace_id, caller_id
    normalized = idempotency_key.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("idempotency_key must be between 1 and 128 characters")
    return normalized


def compute_request_hash(
    model_config_id: UUID | str | None = None,
    messages: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    *,
    tenant_id: UUID | str | None = None,
    workspace_id: UUID | str | None = None,
    actor_id: UUID | str | None = None,
    requested_provider: str | None = None,
    requested_model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """Compute stable SHA-256 hash using RFC 8785 (JCS) canonicalization.

    Supports positional invocation (model_config_id, messages, settings) as well
    as full keyword-based parameter binding. Binds all present execution parameters
    into an immutable cryptographic digest for anti-replay verification.
    """
    payload: dict[str, Any] = {}
    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)
    if workspace_id is not None:
        payload["workspace_id"] = str(workspace_id)
    if actor_id is not None:
        payload["actor_id"] = str(actor_id)
    if model_config_id is not None:
        payload["model_config_id"] = str(model_config_id)
    if requested_provider is not None:
        payload["requested_provider"] = requested_provider
    if requested_model is not None:
        payload["requested_model"] = requested_model
    if messages is not None:
        payload["messages"] = messages
    if tools is not None:
        payload["tools"] = tools
    if response_format is not None:
        payload["response_format"] = response_format
    if settings is not None:
        payload["settings"] = settings
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens

    canonical_bytes = canonicalize_json(payload)
    return hashlib.sha256(canonical_bytes).hexdigest()
