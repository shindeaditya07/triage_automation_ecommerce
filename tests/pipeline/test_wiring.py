"""The app registers the ticket pipeline only when a model is configured."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.api.models.enums import JobType
from services.api.pipeline.wiring import build_pipeline
from services.api.settings import LLMSettings, StoreProfileSettings, StoreSettings

pytestmark = pytest.mark.anyio


def now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("postgresql+asyncpg://u:p@localhost/none")  # never connected
    yield engine
    await engine.dispose()


def llm(**values: object) -> LLMSettings:
    return LLMSettings(_env_file=None, **values)  # type: ignore[arg-type]


GEMINI = {
    "llm_provider": "gemini",
    "llm_api_key": "k",
    "llm_model": "m",
    "judge_provider": "gemini",
    "judge_api_key": "k",
    "judge_model": "m",
    "judge_same_family_ok": True,
    "embed_provider": "gemini",
}


def build(engine: AsyncEngine, settings: LLMSettings):  # type: ignore[no-untyped-def]
    return build_pipeline(
        engine,
        now,
        settings,
        StoreSettings(_env_file=None),  # type: ignore[call-arg]
        StoreProfileSettings(_env_file=None),  # type: ignore[call-arg]
    )


async def test_no_model_means_no_pipeline(engine: AsyncEngine, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="replydesk.pipeline"):
        assert build(engine, llm()) is None
    assert "pipeline disabled" in caplog.text


async def test_a_configured_model_registers_the_handler(engine: AsyncEngine):
    runtime = build(engine, llm(**GEMINI))
    assert runtime is not None
    assert set(runtime.handlers) == {JobType.PROCESS_TICKET}
    await runtime.aclose()


async def test_invalid_settings_disable_the_pipeline_without_leaking_values(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")  # a key and model are missing
    monkeypatch.setenv("LLM_API_KEY", "secret-key-value")
    with caplog.at_level(logging.ERROR, logger="replydesk.pipeline"):
        assert build_pipeline(engine, now) is None
    assert "pipeline disabled" in caplog.text
    assert "secret-key-value" not in caplog.text


async def test_the_log_names_the_missing_setting(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    for name, value in {"LLM_PROVIDER": "gemini", "LLM_API_KEY": "k", "LLM_MODEL": "m"}.items():
        monkeypatch.setenv(name, value)  # no judge model: the common mistake
    with caplog.at_level(logging.ERROR, logger="replydesk.pipeline"):
        assert build_pipeline(engine, now) is None
    assert "JUDGE_MODEL are required" in caplog.text
