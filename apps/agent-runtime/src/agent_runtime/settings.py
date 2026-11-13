from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Runtime configuration. Empty security settings deliberately fail closed."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    auth_store_type: str = "redis"
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0
    runtime_jwt_secret: str = ""
    runtime_jwt_issuer: str = ""
    runtime_jwt_audience: str = "agent-runtime"
    model_gateway_health_url: str = ""
    qdrant_health_url: str = ""
    dependency_timeout_seconds: float = 1.0
    sse_revalidation_interval_seconds: float = Field(default=2.0, gt=0.05, le=4.5)
    sse_auth_lookup_timeout_seconds: float = Field(default=1.5, gt=0.05, le=3.0)
    sse_max_auth_age_seconds: float = Field(default=5.0, gt=0.1, le=5.0)
    mcp_timeout_seconds: float = 10.0
    mcp_max_response_bytes: int = 1_048_576


@lru_cache(maxsize=1)
def get_settings() -> RuntimeSettings:
    return RuntimeSettings()

