"""Builds the job handlers the running app registers, from the environment.

The pipeline needs a model, so a host without one (LLM_PROVIDER unset or `fake`, or settings that do
not validate) still starts and serves everything else; its `process_ticket` jobs then fail with
"no handler" and show up in the job list instead of being drafted by a model that was never set up.
The log names the settings at fault, never their values.
"""

import logging
from dataclasses import dataclass

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.clock import Clock
from services.api.context.client import OrderStoreClient
from services.api.jobs.worker import Handler
from services.api.llm.client import LLMClient
from services.api.llm.errors import LLMConfigError
from services.api.llm.factory import build_llm_client
from services.api.models.enums import JobType
from services.api.pipeline.draft import DraftingConfig
from services.api.pipeline.process import make_process_ticket_handler
from services.api.pipeline.store import PipelineStore
from services.api.policy_source import expectation
from services.api.retrieval.index import PgPolicyIndex
from services.api.settings import LLMSettings, StoreProfileSettings, StoreSettings

log = logging.getLogger("replydesk.pipeline")


@dataclass
class PipelineRuntime:
    handlers: dict[JobType, Handler]
    _client: LLMClient
    _http: httpx.AsyncClient

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._http.aclose()


def build_pipeline(
    engine: AsyncEngine,
    clock: Clock,
    llm_settings: LLMSettings | None = None,
    store_settings: StoreSettings | None = None,
    profile: StoreProfileSettings | None = None,
) -> PipelineRuntime | None:
    """The `process_ticket` handler wired to the model, the order store and the policy index, or
    None when the model is not configured."""
    try:
        llm_settings = llm_settings or LLMSettings()
        store_settings = store_settings or StoreSettings()
        profile = profile or StoreProfileSettings()
    except ValidationError as exc:
        log.error("pipeline disabled: invalid settings %s", [e["loc"] for e in exc.errors()])
        return None
    if llm_settings.llm_provider == "fake":
        log.warning("pipeline disabled: LLM_PROVIDER is not set, so tickets will not be drafted")
        return None
    try:
        client = build_llm_client(llm_settings)
    except LLMConfigError as exc:
        log.error("pipeline disabled: %s", exc)
        return None
    http = httpx.AsyncClient(timeout=store_settings.mock_store_timeout_seconds)
    handler = make_process_ticket_handler(
        PipelineStore(engine),
        client,
        OrderStoreClient(http, store_settings.mock_store_url),
        clock,
        DraftingConfig(
            index=PgPolicyIndex(engine, expectation().version), sign_off=profile.store_sign_off
        ),
    )
    log.info("pipeline enabled: provider=%s", llm_settings.llm_provider)
    return PipelineRuntime({JobType.PROCESS_TICKET: handler}, client, http)
