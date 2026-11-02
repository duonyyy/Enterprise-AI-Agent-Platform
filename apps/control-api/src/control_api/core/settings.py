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

    @field_validator("database_url")
    @classmethod
    def require_postgresql_async(cls, value: str) -> str:
        # Normalize postgresql+asyncpg to postgresql+psycopg if asyncpg is not installed
        if value.startswith("postgresql+asyncpg://"):
            try:
                import asyncpg  # noqa: F401
            except ImportError:
                value = "postgresql+psycopg://" + value.removeprefix("postgresql+asyncpg://")

        if not (value.startswith("postgresql+asyncpg://") or value.startswith("postgresql+psycopg://")):
            raise ValueError(
                "DATABASE_URL must use postgresql+asyncpg or postgresql+psycopg; "
                "local/in-memory databases are forbidden"
            )
        return value


settings = Settings()
