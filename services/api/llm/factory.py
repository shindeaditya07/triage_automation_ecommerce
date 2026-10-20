"""Builds the `LLMClient` the settings ask for."""

from decimal import Decimal

import httpx

from services.api.llm.client import LLMClient
from services.api.llm.errors import LLMConfigError
from services.api.llm.fake import FakeLLMClient
from services.api.llm.gateway import ModelGateway, ModelRoute
from services.api.llm.pricing import Price
from services.api.llm.prompts import PromptRegistry
from services.api.llm.providers.anthropic import AnthropicChat
from services.api.llm.providers.base import ChatModel, Embedder
from services.api.llm.providers.gemini import GeminiEmbedder
from services.api.llm.providers.openai import OpenAIChat, OpenAIEmbedder
from services.api.llm.resilience import CircuitBreaker, RetryPolicy
from services.api.settings import LLMSettings, Provider


def _chat(provider: Provider | None, http: httpx.AsyncClient, key: str, model: str) -> ChatModel:
    if provider == "anthropic":
        return AnthropicChat(http, key, model)
    if provider == "openai":
        return OpenAIChat(http, key, model)
    raise LLMConfigError(f"no adapter for provider {provider!r}")


def _embedder(settings: LLMSettings, http: httpx.AsyncClient, key: str) -> Embedder:
    if settings.embed_provider == "gemini":
        return GeminiEmbedder(http, key, settings.embed_model)
    return OpenAIEmbedder(http, key, settings.embed_model)


def build_llm_client(
    settings: LLMSettings,
    http_client: httpx.AsyncClient | None = None,
    prompts: PromptRegistry | None = None,
) -> LLMClient:
    """`fake` gives a client with nothing recorded (tests add what they need); otherwise a gateway.

    Settings validation has already checked that every key and model is present, so a missing one
    here is a bug and raises `LLMConfigError`.
    """
    if settings.llm_provider == "fake":
        return FakeLLMClient(prompts=prompts or PromptRegistry.load())
    embed_key = settings.embedding_key()
    if not (
        settings.llm_api_key
        and settings.llm_model
        and settings.judge_api_key
        and settings.judge_model
        and embed_key
    ):
        raise LLMConfigError("incomplete LLM settings")
    http = http_client or httpx.AsyncClient(timeout=settings.llm_timeout_seconds)

    def breaker() -> CircuitBreaker:
        return CircuitBreaker(
            settings.llm_breaker_failures, float(settings.llm_breaker_cooldown_seconds)
        )

    drafter = ModelRoute(
        _chat(
            settings.llm_provider, http, settings.llm_api_key.get_secret_value(), settings.llm_model
        ),
        Price(settings.llm_input_price_inr_per_1m, settings.llm_output_price_inr_per_1m),
        breaker(),
        settings.llm_temperature,
    )
    judge = ModelRoute(
        _chat(
            settings.judge_provider,
            http,
            settings.judge_api_key.get_secret_value(),
            settings.judge_model,
        ),
        Price(settings.judge_input_price_inr_per_1m, settings.judge_output_price_inr_per_1m),
        breaker(),
        settings.judge_temperature,
    )
    return ModelGateway(
        drafter=drafter,
        judge=judge,
        embedder=_embedder(settings, http, embed_key.get_secret_value()),
        embed_price=Price(settings.embed_price_inr_per_1m, Decimal(0)),
        embed_breaker=breaker(),
        embed_dimensions=settings.embed_dimensions,
        prompts=prompts or PromptRegistry.load(),
        retry=RetryPolicy(max_attempts=settings.llm_max_attempts),
        http_client=None if http_client else http,
    )
