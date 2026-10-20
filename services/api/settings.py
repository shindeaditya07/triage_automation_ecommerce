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
Provider = Literal["fake", "anthropic", "openai"]
EmbedProvider = Literal["openai", "gemini"]
DEFAULT_EMBED_MODELS: dict[EmbedProvider, str] = {
    "openai": "text-embedding-3-small",
    "gemini": "gemini-embedding-001",
}
DEPLOYED_ENVIRONMENTS = {"staging", "nonprod", "production"}
MIN_SECRET_LENGTH = 32

_OPTIONAL_TEXT_FIELDS = ("frozen_now",)
_OPTIONAL_LLM_FIELDS = (
    "llm_api_key",
    "llm_model",
    "judge_provider",
    "judge_api_key",
    "judge_model",
    "embed_api_key",
    "llm_temperature",
    "judge_temperature",
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


class DeploymentSettings(BaseSettings):
    """Where the process runs and what sits in front of it. Shared by the subset settings below."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Environment = "local"
    # Reverse proxies in front of the API whose X-Forwarded-For entry can be trusted (Render: 1).
    # 0 means "use the socket address", the only safe value when the API faces clients directly.
    trusted_proxy_hops: int = Field(default=0, ge=0)


class AuthSettings(DeploymentSettings):
    """Just what owner authentication needs, so the API can start (and `/healthz` answer) on a
    host that has not been given the rest of the configuration yet."""

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


class JobSettings(BaseSettings):
    """How the background job runner behaves. Every field has a default."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # A job running longer than this is presumed lost and put back in the queue.
    lock_timeout_seconds: int = Field(default=60, gt=0)
    sweep_interval_seconds: int = Field(default=30, gt=0)
    # How long an idle worker waits before asking the database for work again.
    job_poll_interval_seconds: float = Field(default=2.0, gt=0)
    # Set false to run the API without a worker in the same process.
    job_worker_enabled: bool = True


class StoreSettings(BaseSettings):
    """Where the order store is, and how long to wait for it."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mock_store_url: str = "http://localhost:8001"
    mock_store_timeout_seconds: float = Field(default=5.0, gt=0)


class IntakeSettings(DeploymentSettings):
    """Limits and the shared secret for the two ticket-intake endpoints."""

    # Signs the email-ingestion endpoint's requests. Without it that endpoint answers 503.
    hmac_secret: SecretStr | None = None

    form_max_body_bytes: int = Field(default=20_000, gt=0)
    form_message_max_chars: int = Field(default=5_000, gt=0)
    email_max_body_bytes: int = Field(default=262_144, gt=0)
    email_body_max_chars: int = Field(default=20_000, gt=0)
    attachment_max_count: int = Field(default=5, ge=0)
    attachment_max_bytes: int = Field(default=10_485_760, gt=0)
    # Per client address, on the public form only.
    form_ip_max_per_minute: int = Field(default=3, ge=1)
    form_ip_max_per_hour: int = Field(default=10, ge=1)

    @field_validator("hmac_secret", mode="before")
    @classmethod
    def _blank_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _deployed_hmac_secret_is_long(self) -> "IntakeSettings":
        if (
            self.environment in DEPLOYED_ENVIRONMENTS
            and self.hmac_secret is not None
            and len(self.hmac_secret.get_secret_value()) < MIN_SECRET_LENGTH
        ):
            raise ValueError(
                f"HMAC_SECRET must be at least {MIN_SECRET_LENGTH} characters "
                f"when ENVIRONMENT is {self.environment}"
            )
        return self


class LLMSettings(BaseSettings):
    """The language-model layer: which providers, which models, what they cost.

    `fake` replays recorded outputs and needs no key. Anything else needs a key and a model for the
    drafter, and a *different* provider for the judge (docs/adr/0003-llm-provider.md).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Provider = "fake"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    # Sampling temperature sent with classify and draft calls. 0 for repeatable output. Leave it
    # empty (None) for a model that does not accept the parameter.
    llm_temperature: float | None = Field(default=0.0, ge=0, le=2)
    judge_provider: Provider | None = None
    judge_api_key: SecretStr | None = None
    judge_model: str | None = None
    judge_temperature: float | None = Field(default=0.0, ge=0, le=2)
    # By default the judge must come from a different provider than the drafter, so a model never
    # grades its own output. Set true only to accept that on purpose.
    judge_same_family_ok: bool = False
    # Embeddings come from OpenAI or Gemini (EMBED_PROVIDER). With OpenAI the key falls back to
    # LLM_API_KEY / JUDGE_API_KEY when one of them is an OpenAI key; Gemini needs EMBED_API_KEY.
    embed_provider: EmbedProvider = "openai"
    embed_api_key: SecretStr | None = None
    # Blank means the provider's default model (see DEFAULT_EMBED_MODELS).
    embed_model: str = ""
    embed_dimensions: int = EMBEDDING_DIMENSIONS
    llm_timeout_seconds: int = Field(default=30, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1)
    # Consecutive failed calls before the circuit opens, and how long it stays open.
    llm_breaker_failures: int = Field(default=5, ge=1)
    llm_breaker_cooldown_seconds: int = Field(default=30, gt=0)
    # INR per 1M tokens. Decimal, never float.
    llm_input_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    llm_output_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    judge_input_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    judge_output_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)
    embed_price_inr_per_1m: Decimal = Field(default=Decimal(0), ge=0)

    @field_validator("embed_provider", mode="before")
    @classmethod
    def _normalise_embed_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("embed_model", mode="before")
    @classmethod
    def _strip_embed_model(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _default_embed_model(self) -> "LLMSettings":
        if not self.embed_model:
            self.embed_model = DEFAULT_EMBED_MODELS[self.embed_provider]
        return self

    @field_validator("llm_provider", "judge_provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator(*_OPTIONAL_LLM_FIELDS, mode="before")
    @classmethod
    def _blank_llm_value_is_none(cls, value: object) -> object:
        """`JUDGE_MODEL=` in an env file means 'not set'."""
        if isinstance(value, str) and not value.strip():
            return None
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
    def _providers_are_usable(self) -> "LLMSettings":
        if self.llm_provider == "fake":
            return self
        if not (self.llm_api_key and self.llm_model):
            raise ValueError(
                "LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER is not 'fake'"
            )
        if self.judge_provider in (None, "fake") or not (self.judge_api_key and self.judge_model):
            raise ValueError(
                "JUDGE_PROVIDER (not 'fake'), JUDGE_API_KEY and JUDGE_MODEL are required "
                "when LLM_PROVIDER is not 'fake'"
            )
        if self.judge_provider == self.llm_provider and not self.judge_same_family_ok:
            raise ValueError(
                "JUDGE_PROVIDER must differ from LLM_PROVIDER so a model does not grade its own "
                "output (set JUDGE_SAME_FAMILY_OK=true to accept that)"
            )
        if self.embedding_key() is None:
            hint = (
                ", or use an OpenAI key as LLM_API_KEY or JUDGE_API_KEY"
                if self.embed_provider == "openai"
                else ""
            )
            raise ValueError(
                f"embeddings ({self.embed_provider}) need a key: set EMBED_API_KEY{hint}"
            )
        return self

    def embedding_key(self) -> SecretStr | None:
        if self.embed_api_key:
            return self.embed_api_key
        if self.embed_provider == "openai":
            if self.llm_provider == "openai":
                return self.llm_api_key
            if self.judge_provider == "openai":
                return self.judge_api_key
        return None


class Settings(
    DatabaseSettings, AuthSettings, JobSettings, IntakeSettings, LLMSettings, StoreSettings
):
    # API
    hmac_secret: SecretStr
    base_url: str
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Store profile
    store_name: str = "Kaveri Home Goods"
    store_sign_off: str = "Kaveri Home Goods Support"
    store_timezone: str = "Asia/Kolkata"

    # Mock store and time
    frozen_now: datetime | None = None

    # Retention (the job settings are in JobSettings)
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

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "Settings":
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
