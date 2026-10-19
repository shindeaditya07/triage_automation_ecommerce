"""Typed settings, read once from environment variables (and a local .env file).

This is the only place the application reads configuration. A missing or invalid required value
raises a ValidationError naming the variable, so a misconfigured deploy fails at startup.
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from services.api.models.records import EMBEDDING_DIMENSIONS

Environment = Literal["local", "ci", "staging", "nonprod", "production"]
DEPLOYED_ENVIRONMENTS = {"staging", "nonprod", "production"}
MIN_SECRET_LENGTH = 32

_OPTIONAL_TEXT_FIELDS = (
    "llm_api_key",
    "llm_model",
    "judge_api_key",
    "judge_model",
    "frozen_now",
)


class DatabaseSettings(BaseSettings):
    """Just the database URL, so migrations don't need every other variable."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("must start with 'postgresql+asyncpg://' (the asyncpg driver form)")
        return value


class AuthSettings(BaseSettings):
    """Just what owner authentication needs, so the API can start (and `/healthz` answer) on a
    host that has not been given the rest of the configuration yet."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Environment = "local"
    # Signs the session tokens. Random, 32+ characters, different in every environment.
    secret_key: SecretStr
    owner_email: str | None = None
    owner_password_hash: SecretStr | None = None  # argon2 hash; see `make owner-hash`

    access_token_minutes: int = Field(default=15, gt=0)
    refresh_token_days: int = Field(default=7, gt=0)
    # Consecutive failed logins before the account locks; each further failure doubles the lock.
    login_max_failures: int = Field(default=5, ge=1)
    login_lockout_base_seconds: int = Field(default=60, gt=0)
    login_lockout_max_seconds: int = Field(default=3600, gt=0)
    login_ip_max_attempts: int = Field(default=10, ge=1)
    login_ip_window_seconds: int = Field(default=300, gt=0)
    # Reverse proxies in front of the API whose X-Forwarded-For entry can be trusted (Render: 1).
    # 0 means "use the socket address", the only safe value when the API faces clients directly.
    trusted_proxy_hops: int = Field(default=0, ge=0)

    @field_validator("owner_email", "owner_password_hash", mode="before")
    @classmethod
    def _blank_credential_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("owner_email")
    @classmethod
    def _normalise_owner_email(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value

    @model_validator(mode="after")
    def _deployed_secret_is_long(self) -> "AuthSettings":
        if (
            self.environment in DEPLOYED_ENVIRONMENTS
            and len(self.secret_key.get_secret_value()) < MIN_SECRET_LENGTH
        ):
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters "
                f"when ENVIRONMENT is {self.environment}"
            )
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.environment in DEPLOYED_ENVIRONMENTS


class Settings(DatabaseSettings, AuthSettings):
    # API
    hmac_secret: SecretStr
    base_url: str
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # LLM. "fake" uses recorded outputs and needs no key.
    llm_provider: str = "fake"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    judge_api_key: SecretStr | None = None
    judge_model: str | None = None
    embed_model: str = "text-embedding-3-small"
    embed_dimensions: int = EMBEDDING_DIMENSIONS
    llm_timeout_seconds: int = Field(default=30, gt=0)
    # INR per 1M tokens. Decimal, never float.
    llm_input_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    llm_output_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    judge_input_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    judge_output_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    embed_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)

    # Store profile
    store_name: str = "Kaveri Home Goods"
    store_sign_off: str = "Kaveri Home Goods Support"
    store_timezone: str = "Asia/Kolkata"

    # Mock store and time
    mock_store_url: str = "http://localhost:8001"
    frozen_now: datetime | None = None

    # Jobs and retention
    lock_timeout_seconds: int = Field(default=60, gt=0)
    sweep_interval_seconds: int = Field(default=30, gt=0)
    retention_days: int = Field(default=90, ge=1)

    # Policy
    policy_file_path: Path = Path("docs/policies.md")
    policy_version: str = "1.1"

    @field_validator(*_OPTIONAL_TEXT_FIELDS, mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """`FROZEN_NOW=` in an env file means 'not set'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_provider")
    @classmethod
    def _normalise_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("store_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("frozen_now")
    @classmethod
    def _frozen_now_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("must include a timezone, e.g. 2026-10-15T12:00:00Z")
        return value

    @field_validator("embed_dimensions")
    @classmethod
    def _matches_table_width(cls, value: int) -> int:
        if value != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"must be {EMBEDDING_DIMENSIONS}: the policy_embeddings column has that width, "
                "so another size needs a migration first"
            )
        return value

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "Settings":
        if self.llm_provider != "fake" and not (self.llm_api_key and self.llm_model):
            raise ValueError(
                "LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER is not 'fake'"
            )
        if self.environment in DEPLOYED_ENVIRONMENTS:
            if self.frozen_now is not None:
                raise ValueError(f"FROZEN_NOW must be empty when ENVIRONMENT is {self.environment}")
            if len(self.hmac_secret.get_secret_value()) < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"HMAC_SECRET must be at least {MIN_SECRET_LENGTH} characters "
                    f"when ENVIRONMENT is {self.environment}"
                )
        if self.environment == "production" and self.llm_provider == "fake":
            raise ValueError("LLM_PROVIDER must not be 'fake' in production")
        return self


def get_settings() -> Settings:
    """Load and validate settings. Raises pydantic.ValidationError naming each bad variable."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
