import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.api.models.records import EMBEDDING_DIMENSIONS
from services.api.settings import DatabaseSettings, Settings

ROOT = Path(__file__).resolve().parents[2]
DB_URL = "postgresql+asyncpg://u:p@localhost:5432/d"
GOOD = "x" * 40
GEMINI_KEY = "g1"  # pragma: allowlist secret
# A drafter and a judge from different providers: the smallest valid non-fake LLM setup.
REAL_MODELS = {
    "LLM_PROVIDER": "anthropic",
    "LLM_API_KEY": "k1",  # pragma: allowlist secret
    "LLM_MODEL": "drafter",
    "JUDGE_PROVIDER": "openai",
    "JUDGE_API_KEY": "k2",  # pragma: allowlist secret
    "JUDGE_MODEL": "judge",
}


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
        FROZEN_NOW="2026-10-15T12:00:00Z",
        **REAL_MODELS,
    )
    with pytest.raises(ValidationError, match="FROZEN_NOW"):
        Settings()  # type: ignore[call-arg]


def test_deployed_environments_reject_short_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ENVIRONMENT="staging", SECRET_KEY="short")
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


def test_a_real_setup_loads_and_embeds_with_the_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, **REAL_MODELS)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.embedding_key() is not None
    assert settings.embedding_key().get_secret_value() == "k2"  # type: ignore[union-attr]


def test_provider_names_are_case_insensitive_and_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, **(REAL_MODELS | {"LLM_PROVIDER": " Anthropic "}))
    assert Settings().llm_provider == "anthropic"  # type: ignore[call-arg]
    _env(monkeypatch, LLM_PROVIDER="mystery")
    with pytest.raises(ValidationError, match="llm_provider"):
        Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize("missing", ["JUDGE_PROVIDER", "JUDGE_API_KEY", "JUDGE_MODEL"])
def test_a_real_drafter_needs_a_judge(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _env(monkeypatch, **{k: v for k, v in REAL_MODELS.items() if k != missing})
    with pytest.raises(ValidationError, match="JUDGE_PROVIDER"):
        Settings()  # type: ignore[call-arg]


def test_the_judge_cannot_be_the_drafters_provider_unless_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same = REAL_MODELS | {
        "JUDGE_PROVIDER": "anthropic",
        "EMBED_API_KEY": "k3",  # pragma: allowlist secret
    }
    _env(monkeypatch, **same)
    with pytest.raises(ValidationError, match="grade its own"):
        Settings()  # type: ignore[call-arg]
    _env(monkeypatch, **(same | {"JUDGE_SAME_FAMILY_OK": "true"}))
    assert Settings().judge_provider == "anthropic"  # type: ignore[call-arg]


def test_embeddings_need_an_openai_key_somewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    same = REAL_MODELS | {"JUDGE_PROVIDER": "anthropic", "JUDGE_SAME_FAMILY_OK": "true"}
    _env(monkeypatch, **same)
    with pytest.raises(ValidationError, match="OpenAI key"):
        Settings()  # type: ignore[call-arg]


def test_gemini_embeddings_need_their_own_key_and_default_to_their_own_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An OpenAI judge key is not a Gemini key.
    _env(monkeypatch, **(REAL_MODELS | {"EMBED_PROVIDER": "gemini"}))
    with pytest.raises(ValidationError, match="embeddings \\(gemini\\) need a key"):
        Settings()  # type: ignore[call-arg]
    _env(monkeypatch, **(REAL_MODELS | {"EMBED_PROVIDER": " Gemini ", "EMBED_API_KEY": GEMINI_KEY}))
    settings = Settings()  # type: ignore[call-arg]
    assert (settings.embed_provider, settings.embed_model) == ("gemini", "gemini-embedding-001")
    assert settings.embedding_key().get_secret_value() == GEMINI_KEY  # type: ignore[union-attr]


def test_the_embedding_model_defaults_by_provider_and_can_be_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)
    assert Settings().embed_model == "text-embedding-3-small"  # type: ignore[call-arg]
    _env(monkeypatch, EMBED_MODEL="  ")
    assert Settings().embed_model == "text-embedding-3-small"  # type: ignore[call-arg]
    _env(monkeypatch, EMBED_PROVIDER="gemini", EMBED_MODEL="my-model")
    assert Settings().embed_model == "my-model"  # type: ignore[call-arg]
    _env(monkeypatch, EMBED_PROVIDER="mystery")
    with pytest.raises(ValidationError, match="embed_provider"):
        Settings()  # type: ignore[call-arg]


def test_blank_judge_and_temperature_values_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, JUDGE_PROVIDER="", JUDGE_MODEL="", LLM_TEMPERATURE="")
    settings = Settings()  # type: ignore[call-arg]
    assert (settings.judge_provider, settings.judge_model, settings.llm_temperature) == (
        None,
        None,
        None,
    )
