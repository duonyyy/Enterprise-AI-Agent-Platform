from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings for Control Plane API."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_default=True)

    app_name: str = "Control Plane API v2.0"
    environment: str = "development"
    database_url: str
    runtime_api_url: str = "http://localhost:8001"
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_issuer: str = "http://localhost:8080/realms/enterprise"
    jwt_audience: str = "enterprise-control-api"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # Redis & Shared Auth Store Settings (AU-02 / Finding R01)
    redis_url: str = "redis://127.0.0.1:6379/0"
    auth_store_type: str = "redis"
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0

    # Runtime Delegation Profile (AU-05 / Finding R06)
    runtime_jwt_secret: SecretStr = Field(default=SecretStr(""))
    runtime_jwt_issuer: str = "http://localhost:8080/realms/enterprise"
    runtime_jwt_audience: str = "agent-runtime"

    # OIDC & Session Authentication Settings (SPEC-AUTH-001 / Step 02)
    oidc_issuer: str = "http://localhost:8080/realms/enterprise"
    oidc_client_id: str = "enterprise-studio"
    oidc_client_secret: SecretStr = Field(
        default=SecretStr("enterprise-studio-client-secret-dev-default-32ch")
    )
    oidc_redirect_uri: str = "http://localhost:3000/v1/auth/callback"
    oidc_redirect_allowlist: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    session_idle_timeout_seconds: int = Field(default=1800, ge=60, le=86400)
    session_absolute_timeout_seconds: int = Field(default=28800, ge=300, le=604800)
    session_max_accepted_absolute_lifetime_seconds: int = Field(default=28800, ge=300, le=604800)
    session_clock_skew_seconds: int = Field(default=60, ge=0, le=600)
    session_cookie_name: str = "agent_session"
    session_cookie_secure: bool = False
    session_cookie_same_site: str = "lax"
    session_cookie_path: str = "/"

    # Pre-auth browser correlation cookie (RFC 9700 §2.1.1 / Finding R02)
    oidc_correlation_cookie_name: str = "agent_oidc_corr"
    oidc_correlation_cookie_secure: bool = False
    oidc_correlation_cookie_same_site: str = "lax"

    @field_validator("database_url")
    @classmethod
    def require_postgresql_async(cls, value: str) -> str:
        # Normalize postgresql+asyncpg to postgresql+psycopg if asyncpg is not installed
        if value.startswith("postgresql+asyncpg://"):
            try:
                import asyncpg  # noqa: F401
            except ImportError:
                value = "postgresql+psycopg://" + value.removeprefix("postgresql+asyncpg://")

        if not value.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
            raise ValueError(
                "DATABASE_URL must use postgresql+asyncpg or postgresql+psycopg; "
                "local/in-memory databases are forbidden"
            )
        return value


settings = Settings()
