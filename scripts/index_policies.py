"""Embed docs/policies.md into the policy index, so /readyz can pass and retrieval can run.

    make index-policies

Needs only DATABASE_URL and the LLM settings (the embedding key and model), from the environment or
.env. Safe to run again: a passage whose text and embedding model already match is left alone.
Prints counts only.
"""

import asyncio
import sys

from services.api.db.engine import create_engine
from services.api.llm.factory import build_llm_client
from services.api.llm.fake import FakeLLMClient
from services.api.policy_source import policies_path
from services.api.retrieval.index import build_index
from services.api.settings import DatabaseSettings, LLMSettings


class IndexSettings(DatabaseSettings, LLMSettings):
    """The two groups of settings this command uses, so it does not need the API's secrets."""


async def main() -> int:
    settings = IndexSettings()  # type: ignore[call-arg]  # database_url comes from the environment
    llm = build_llm_client(settings)
    embed_model = FakeLLMClient.model if settings.llm_provider == "fake" else settings.embed_model
    engine = create_engine(settings.database_url)
    try:
        result = await build_index(
            engine,
            llm,
            policies_text=policies_path().read_text(encoding="utf-8"),
            embed_model=embed_model,
        )
    finally:
        await engine.dispose()
    cost = sum(call.cost_inr for call in result.calls)
    sys.stdout.write(
        f"policy version {result.version}: {result.embedded} embedded, "
        f"{result.unchanged} unchanged, cost INR {cost}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
