from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Runtime configuration. Empty security settings deliberately fail closed."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    environment: str = Field(
        default="development", validation_alias=AliasChoices("ENVIRONMENT", "ENV")
    )
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"
    auth_store_type: str = "redis"
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0
    runtime_jwt_secret: str = ""
    runtime_jwt_issuer: str = ""
    runtime_jwt_audience: str = "agent-runtime"
    model_gateway_health_url: str = ""
    model_gateway_config_path: str = ""
    model_gateway_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    model_gateway_allow_local_endpoints: bool = False
    e5_model_snapshot: str = ""
    qdrant_health_url: str = ""
    dependency_timeout_seconds: float = 1.0
    sse_revalidation_interval_seconds: float = Field(default=2.0, gt=0.05, le=4.5)
    sse_auth_lookup_timeout_seconds: float = Field(default=1.5, gt=0.05, le=3.0)
    sse_max_auth_age_seconds: float = Field(default=5.0, gt=0.1, le=5.0)
    sse_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=1.0)
    sse_query_timeout_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    sse_send_timeout_seconds: float = Field(default=2.0, ge=0.1, le=5.0)
    sse_heartbeat_interval_seconds: float = Field(default=15.0, ge=5.0, le=60.0)
    sse_max_stream_duration_seconds: float = Field(default=900.0, ge=10.0, le=1800.0)
    sse_max_connections_total: int = Field(default=100, ge=1, le=100)
    sse_max_connections_per_workspace: int = Field(default=50, ge=1, le=50)
    sse_max_connections_per_user_workspace: int = Field(default=5, ge=1, le=5)
    mcp_timeout_seconds: float = 10.0
    mcp_max_response_bytes: int = 1_048_576
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "agent-platform-docs"
    s3_region: str = "us-east-1"
    s3_timeout_seconds: float = Field(default=5.0, gt=0.1, le=60.0)
    run_data_max_input_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    run_data_max_output_bytes: int = Field(default=4_194_304, ge=1024, le=20_971_520)
    run_data_default_retention_days: int = Field(default=30, ge=1, le=36500)
    run_admission_max_queued_per_workspace: int = Field(default=1000, ge=1, le=100_000)
    run_admission_execution_enabled: bool = False
    run_queue_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    run_maintenance_mutations_enabled: bool = False
    run_execution_grants_enabled: bool = False
    run_execution_key: str = ""
    run_execution_key_id: str = "key-exec-v1"
    run_worker_execution_enabled: bool = False
    run_execution_max_wall_time_seconds: int = Field(default=180, ge=1, le=900)
    run_lease_duration_seconds: int = Field(default=30, ge=30, le=120)
    run_heartbeat_interval_seconds: int = Field(default=5, ge=1, le=10)
    run_max_claim_attempts: int = Field(default=3, ge=1, le=3)
    run_workspace_concurrency_cap: int = Field(default=2, ge=1, le=10)


@lru_cache(maxsize=1)
def get_settings() -> RuntimeSettings:
    return RuntimeSettings()
