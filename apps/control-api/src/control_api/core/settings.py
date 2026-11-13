import json
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Redis & Shared Auth Store Settings (AU-02 / Finding R01 / Finding C04)
    redis_url: str = "redis://127.0.0.1:6379/0"
    auth_store_type: Literal["redis", "memory"] = "redis"
    redis_socket_timeout_seconds: float = 2.0
    redis_connect_timeout_seconds: float = 2.0

    # Runtime Delegation Profile (AU-05 / Finding R06)
    runtime_jwt_secret: SecretStr = Field(default=SecretStr(""))
    runtime_jwt_issuer: str = "http://localhost:8080/realms/enterprise"
    runtime_jwt_audience: str = "agent-runtime"

    # OIDC & Session Authentication Settings (SPEC-AUTH-001 / Step 02)
    oidc_issuer: str = "http://localhost:8080/realms/enterprise"
    oidc_client_id: str = "enterprise-studio"
    # Additional ID Token audiences require explicit trust, as well as matching azp.
    oidc_additional_trusted_audiences: Annotated[list[str], NoDecode] = Field(
        default_factory=list, max_length=64
    )
    oidc_client_secret: SecretStr = Field(
        default=SecretStr("enterprise-studio-client-secret-dev-default-32ch")
    )
    oidc_redirect_uri: str = "http://localhost:3000/v1/auth/callback"
    oidc_redirect_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(
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

    # Workspace & Tenant Bootstrap Authority Allowlist (SPEC-WORKSPACE-001 / WS-D01)
    # Entries may be "<issuer>|<subject>" or "<subject>" (bound to oidc_issuer).
    bootstrap_operator_subjects: Annotated[list[str], NoDecode] = Field(
        default_factory=list, max_length=64
    )

    # Workspace Invitations & Proof Encryption (ADR-019 / Step 03-B)
    invitations_enabled: bool = False
    invitation_public_app_origin: str = "http://localhost:3000"
    invitation_proof_encryption_key: SecretStr = Field(default=SecretStr(""))
    invitation_proof_key_id: str = "v1"
    invitation_ttl_hours: int = Field(default=72, ge=1, le=720)

    # Invitation Acceptance & Encrypted Handoff (ADR-020 / Step 03-C)
    invitation_handoff_encryption_key: SecretStr = Field(default=SecretStr(""))
    invitation_handoff_key_id: str = "v1"
    invitation_handoff_ttl_seconds: int = Field(default=600, ge=60, le=900)
    invitation_identity_freshness_seconds: int = Field(default=900, ge=60, le=3600)
    invitation_identity_max_future_skew_seconds: int = Field(default=60, ge=0, le=300)
    invitation_handoff_cookie_name: str = "agent_invite_handoff"
    invitation_handoff_cookie_secure: bool = False
    invitation_handoff_cookie_same_site: str = "lax"
    invitation_handoff_cookie_path: str = "/"
    invitation_handoff_browser_rate_limit: int = Field(default=5, ge=1, le=100)
    invitation_handoff_ip_rate_limit: int = Field(default=30, ge=1, le=1000)
    invitation_handoff_rate_window_seconds: int = Field(default=600, ge=10, le=3600)
    trusted_proxy_cidrs: tuple[str, ...] = ()
    invitation_resend_cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    invitation_recipient_hourly_limit: int = Field(default=5, ge=1, le=1000)
    invitation_workspace_hourly_limit: int = Field(default=100, ge=1, le=10000)

    def is_bootstrap_operator(self, issuer: str, subject: str) -> bool:
        """Check whether (issuer, subject) matches an authorized bootstrap operator entry (WS-D01)."""
        clean_iss = issuer.strip()
        clean_sub = subject.strip()
        if not clean_iss or not clean_sub:
            return False
        for raw_entry in self.bootstrap_operator_subjects:
            entry = raw_entry.strip()
            if not entry:
                continue
            if "|" in entry:
                cfg_iss, cfg_sub = entry.split("|", 1)
                if cfg_iss.strip() == clean_iss and cfg_sub.strip() == clean_sub:
                    return True
            elif clean_iss == self.oidc_issuer.strip() and entry == clean_sub:
                return True
        return False

    @field_validator(
        "oidc_additional_trusted_audiences",
        "oidc_redirect_allowlist",
        "allowed_origins",
        "bootstrap_operator_subjects",
        mode="before",
    )
    @classmethod
    def parse_string_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return []
            if trimmed.startswith("["):
                return json.loads(trimmed)
            return [item.strip() for item in trimmed.split(",") if item.strip()]
        return value

    @field_validator("auth_store_type", mode="before")
    @classmethod
    def normalize_auth_store_type(cls, value: Any) -> str:
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ("redis", "memory"):
                return val
            raise ValueError(f"Invalid auth_store_type '{value}'. Must be 'redis' or 'memory'")
        raise ValueError(f"auth_store_type must be a string, got {type(value).__name__}")

    @model_validator(mode="after")
    def validate_production_auth_store(self) -> "Settings":
        if self.environment.strip().lower() in ("production", "staging") and self.auth_store_type == "memory":
            raise ValueError(
                "In-memory auth store is strictly forbidden in production and staging environments"
            )
        return self

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
