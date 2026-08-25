"""Tests for OllamaProvider: thin-wrapper guarantees + /api/tags health check."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from agentalyze.providers.base import ChatMessage
from agentalyze.providers.ollama import DEFAULT_BASE_URL, OllamaProvider, _root_from_base_url
from agentalyze.providers.openai_compatible import OpenAICompatibleProvider

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"
TAGS_URL = "http://localhost:11434/api/tags"


def _provider(model_name: str = "llama3.1:8b") -> OllamaProvider:
    return OllamaProvider(model_name=model_name, health_check_timeout_seconds=1.0)


class TestThinWrapper:
    """OllamaProvider must reuse the OpenAI-compatible logic, not duplicate it."""

    def test_is_a_subclass_of_openai_compatible_provider(self) -> None:
        provider = _provider()

        assert isinstance(provider, OpenAICompatibleProvider)

    def test_overrides_only_health_check(self) -> None:
        overridden = set(OllamaProvider.__dict__) & {"chat_completion", "health_check"}

        assert overridden == {"health_check"}

    def test_defaults(self) -> None:
        provider = _provider()

        assert DEFAULT_BASE_URL == "http://localhost:11434/v1"
        assert provider._base_url == DEFAULT_BASE_URL
        assert provider.name == "llama3.1:8b-local"

    def test_root_derivation_from_base_url(self) -> None:
        assert _root_from_base_url("http://localhost:11434/v1") == "http://localhost:11434"
        assert _root_from_base_url("http://localhost:11434") == "http://localhost:11434"


@respx.mock
async def test_chat_completion_reuses_parent_mapping() -> None:
    payload = {
        "id": "chatcmpl-ollama",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "llama3.1:8b",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    route = respx.post(CHAT_URL).mock(Response(200, json=payload))
    provider = _provider()

    result = await provider.chat_completion([ChatMessage(role="user", content="hello")])

    assert route.called
    assert result.message.content == "Hi there"
    assert result.total_tokens == 7
    # The request went through the shared parent mapping with our model name.
    body = route.calls.last.request.content
    assert b"llama3.1:8b" in body


@respx.mock
async def test_health_check_true_when_model_is_installed() -> None:
    respx.get(TAGS_URL).mock(
        Response(
            200,
            json={"models": [{"name": "nomic-embed-text"}, {"name": "llama3.1:8b"}]},
        )
    )

    assert await _provider().health_check() is True


@respx.mock
async def test_health_check_false_when_server_alive_but_model_missing() -> None:
    # Server answered fine — but the model we need is not installed.
    respx.get(TAGS_URL).mock(Response(200, json={"models": [{"name": "mistral"}]}))

    assert await _provider().health_check() is False


@pytest.mark.parametrize(
    ("status_code", "body"),
    [(404, {"error": "not found"}), (200, "not-json")],
)
async def test_health_check_returns_false_on_bad_tags_response(status_code: int, body: object) -> None:
    with respx.mock:
        respx.get(TAGS_URL).mock(Response(status_code, json=body))
        assert await _provider().health_check() is False


async def test_health_check_returns_false_when_server_unreachable() -> None:
    with respx.mock:
        respx.get(TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
        assert await _provider().health_check() is False


@respx.mock
async def test_custom_base_url_points_at_that_server() -> None:
    tags_url = "http://192.168.1.10:11434/api/tags"
    respx.get(tags_url).mock(Response(200, json={"models": [{"name": "qwen2.5"}]}))

    provider = OllamaProvider(
        model_name="qwen2.5", base_url="http://192.168.1.10:11434/v1"
    )

    assert await provider.health_check() is True
