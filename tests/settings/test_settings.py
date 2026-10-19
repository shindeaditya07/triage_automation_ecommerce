import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.api.models.records import EMBEDDING_DIMENSIONS
from services.api.settings import DatabaseSettings, Settings

ROOT = Path(__file__).resolve().parents[2]
DB_URL = "postgresql+asyncpg://u:p@localhost:5432/d"
GOOD = "x" * 40


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty environment and ignore any local .env file."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(ROOT / "tests")


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    base = {
        "DATABASE_URL": DB_URL,
        "SECRET_KEY": GOOD,
        "HMAC_SECRET": GOOD,
        "BASE_URL": "http://localhost:8000",
    }
    for key, value in (base | values).items():
        monkeypatch.setenv(key, value)


def test_defaults_load(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.environment == "local"
    assert settings.llm_provider == "fake"
    assert settings.embed_dimensions == EMBEDDING_DIMENSIONS
    assert settings.cors_allowed_origins == []


def test_missing_required_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(ValidationError, match="secret_key"):
        Settings()  # type: ignore[call-arg]


def test_database_url_needs_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match="asyncpg"):
        DatabaseSettings(database_url="postgresql://u:p@h/d")


def test_cors_origins_split_on_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, CORS_ALLOWED_ORIGINS="https://a.example, https://b.example,")
    assert Settings().cors_allowed_origins == [  # type: ignore[call-arg]
        "https://a.example",
        "https://b.example",
    ]


def test_blank_optional_values_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, FROZEN_NOW="", LLM_MODEL="", OWNER_EMAIL="")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.frozen_now is None
    assert settings.llm_model is None
    assert settings.owner_email is None


def test_frozen_now_needs_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, FROZEN_NOW="2026-10-15T12:00:00")
    with pytest.raises(ValidationError, match="timezone"):
        Settings()  # type: ignore[call-arg]


def test_real_provider_needs_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, LLM_PROVIDER="anthropic")
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        Settings()  # type: ignore[call-arg]


def test_embedding_width_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, EMBED_DIMENSIONS="768")
    with pytest.raises(ValidationError, match="1536"):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("environment", ["staging", "nonprod", "production"])
def test_deployed_environments_reject_frozen_clock(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    _env(
        monkeypatch,
        ENVIRONMENT=environment,
        LLM_PROVIDER="anthropic",
        LLM_API_KEY="k",
        LLM_MODEL="m",
        FROZEN_NOW="2026-10-15T12:00:00Z",
    )
    with pytest.raises(ValidationError, match="FROZEN_NOW"):
        Settings()  # type: ignore[call-arg]


def test_deployed_environments_reject_short_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ENVIRONMENT="staging", SECRET_KEY="short")  # noqa: S106
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings()  # type: ignore[call-arg]


def test_production_rejects_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ENVIRONMENT="production")
    with pytest.raises(ValidationError, match="production"):
        Settings()  # type: ignore[call-arg]


def test_secrets_are_not_leaked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    assert GOOD not in repr(Settings())  # type: ignore[call-arg]


def test_env_example_lists_every_setting() -> None:
    """Drift guard: a new Settings field must be documented in .env.example, and vice versa."""
    text = (ROOT / ".env.example").read_text()
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))
    fields = {name.upper() for name in Settings.model_fields}
    assert fields - documented == set(), "missing from .env.example"
    # PORT is read by uvicorn/Render, not by Settings.
    assert documented - fields - {"PORT"} == set(), "in .env.example but not in Settings"
