import json
from typing import Any

import httpx
import pytest

from services.api.llm.errors import (
    LLMConnectionError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
)
from services.api.llm.providers.anthropic import AnthropicChat
from services.api.llm.providers.openai import OpenAIChat, OpenAIEmbedder

pytestmark = pytest.mark.anyio
KEY = "test-key-value"  # pragma: allowlist secret
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}
ASK: dict[str, Any] = {
    "system": "sys",
    "user": "usr",
    "schema": SCHEMA,
    "schema_name": "thing",
    "max_tokens": 50,
    "temperature": 0.0,
}


def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def reply(status: int = 200, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {}, headers=headers)

    return handler


async def test_anthropic_sends_a_forced_tool_call_and_reads_the_input() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [{"type": "text", "text": "x"}, {"type": "tool_use", "input": {"a": 1}}],
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        )

    async with client(handler) as http:
        out = await AnthropicChat(http, KEY, "m1").complete(**ASK)
    assert (out.data, out.input_tokens, out.output_tokens) == ({"a": 1}, 11, 7)
    assert seen["headers"]["x-api-key"] == KEY
    assert seen["headers"]["anthropic-version"]
    body = seen["body"]
    assert body["tool_choice"] == {"type": "tool", "name": "thing"}
    assert body["tools"][0]["input_schema"] == SCHEMA
    assert body["temperature"] == 0.0 and body["max_tokens"] == 50 and body["system"] == "sys"


async def test_temperature_is_omitted_when_none() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "tool_use", "input": {}}]})

    async with client(handler) as http:
        await AnthropicChat(http, KEY, "m").complete(**(ASK | {"temperature": None}))
    assert "temperature" not in seen["body"]


@pytest.mark.parametrize(
    "payload",
    [
        {"stop_reason": "max_tokens", "content": []},
        {"content": [{"type": "text", "text": "no tool"}]},
        {"content": [{"type": "tool_use", "input": "not an object"}]},
    ],
)
async def test_anthropic_unusable_answers(payload: dict[str, Any]) -> None:
    async with client(reply(200, payload)) as http:
        with pytest.raises(LLMInvalidOutputError):
            await AnthropicChat(http, KEY, "m").complete(**ASK)


@pytest.mark.parametrize(
    ("status", "error", "retry_after"),
    [
        (429, LLMRateLimitError, 3.0),
        (500, LLMServerError, None),
        (503, LLMServerError, None),
        (529, LLMServerError, None),
        (400, LLMRequestError, None),
        (401, LLMRequestError, None),
        (404, LLMRequestError, None),
    ],
)
async def test_http_statuses_map_to_the_right_error(
    status: int, error: type[Exception], retry_after: float | None
) -> None:
    headers = {"retry-after": "3"} if retry_after else None
    secret_body = {"error": {"type": "invalid_request_error", "message": "customer text here"}}
    async with client(reply(status, secret_body, headers)) as http:
        with pytest.raises(error) as caught:
            await OpenAIChat(http, KEY, "m").complete(**ASK)
    assert getattr(caught.value, "retry_after", None) == retry_after
    assert "customer text" not in str(caught.value)
    assert KEY not in str(caught.value)


async def test_timeouts_and_transport_errors_are_transient() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with client(timeout) as http:
        with pytest.raises(LLMTimeoutError):
            await OpenAIChat(http, KEY, "m").complete(**ASK)
    async with client(refused) as http:
        with pytest.raises(LLMConnectionError):
            await OpenAIChat(http, KEY, "m").complete(**ASK)


async def test_a_non_json_reply_is_invalid_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    async with client(handler) as http:
        with pytest.raises(LLMInvalidOutputError, match="not JSON"):
            await OpenAIChat(http, KEY, "m").complete(**ASK)


def openai_answer(content: Any, finish: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
    }


async def test_openai_sends_a_json_schema_and_parses_the_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=openai_answer('{"a": 2}'))

    async with client(handler) as http:
        out = await OpenAIChat(http, KEY, "m2").complete(**ASK)
    assert (out.data, out.input_tokens, out.output_tokens) == ({"a": 2}, 20, 5)
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert seen["body"]["max_completion_tokens"] == 50
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    "payload",
    [
        openai_answer('{"a": 1', "length"),
        openai_answer("not json"),
        openai_answer("[1, 2]"),
        openai_answer(None),
        {"choices": []},
    ],
)
async def test_openai_unusable_answers(payload: dict[str, Any]) -> None:
    async with client(reply(200, payload)) as http:
        with pytest.raises(LLMInvalidOutputError):
            await OpenAIChat(http, KEY, "m").complete(**ASK)


async def test_embeddings_batch_check_length_and_sum_tokens() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        sizes.append(len(texts))
        # Out of order on purpose: the adapter must sort by index.
        data = [{"index": i, "embedding": [float(i)] * 4} for i in reversed(range(len(texts)))]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": len(texts)}})

    async with client(handler) as http:
        vectors, tokens = await OpenAIEmbedder(http, KEY, "emb").embed(
            [f"t{i}" for i in range(250)], 4
        )
    assert sizes == [100, 100, 50] and tokens == 250
    assert [v[0] for v in vectors[:3]] == [0.0, 1.0, 2.0]


async def test_embeddings_of_the_wrong_width_or_count_are_rejected() -> None:
    wrong_width = {"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {}}
    wrong_count = {"data": [], "usage": {}}
    for payload in (wrong_width, wrong_count):
        async with client(reply(200, payload)) as http:
            with pytest.raises(LLMInvalidOutputError):
                await OpenAIEmbedder(http, KEY, "emb").embed(["a"], 4)
