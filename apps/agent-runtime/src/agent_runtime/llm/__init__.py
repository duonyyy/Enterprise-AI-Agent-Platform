"""Model Gateway ports, error taxonomy, token profiling, and scoped secrets."""

from agent_runtime.llm.embedding_adapter import E5EmbeddingAdapter
from agent_runtime.llm.errors import (
    ModelAuthError,
    ModelBudgetExceededError,
    ModelContextTooLargeError,
    ModelGatewayError,
    ModelInvalidResponseError,
    ModelPolicyDeniedError,
    ModelRateLimitedError,
    ModelTimeoutError,
    ModelUnavailableError,
    SecretAccessDeniedError,
    SecretNotFoundError,
)
from agent_runtime.llm.ports import (
    EmbeddingPort,
    GenerationPort,
    SecretResolverPort,
    StreamingPort,
    TokenProfilePort,
)
from agent_runtime.llm.profile import TokenProfiler, estimate_tokens_heuristic
from agent_runtime.llm.secrets import RedactedSecret, ScopedSecretResolver

__all__ = [
    "E5EmbeddingAdapter",
    "EmbeddingPort",
    "GenerationPort",
    "ModelAuthError",
    "ModelBudgetExceededError",
    "ModelContextTooLargeError",
    "ModelGatewayError",
    "ModelInvalidResponseError",
    "ModelPolicyDeniedError",
    "ModelRateLimitedError",
    "ModelTimeoutError",
    "ModelUnavailableError",
    "RedactedSecret",
    "ScopedSecretResolver",
    "SecretAccessDeniedError",
    "SecretNotFoundError",
    "SecretResolverPort",
    "StreamingPort",
    "TokenProfilePort",
    "TokenProfiler",
    "estimate_tokens_heuristic",
]
