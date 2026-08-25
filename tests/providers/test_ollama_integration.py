"""Optional integration test against a REAL local Ollama server.

Excluded from the default run via the ``requires_ollama`` marker (see
``pyproject.toml``); opt in with ``pytest -m requires_ollama``. Requires a
running Ollama on localhost:11434 with the model pulled locally.
"""

from __future__ import annotations

import httpx
import pytest

from agentalyze.providers.base import ChatMessage
from agentalyze.providers.ollama import OllamaProvider

pytestmark = pytest.mark.requires_ollama


async def _ollama_is_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            (await client.get("http://localhost:11434/api/tags")).raise_for_status()
    except Exception:  # noqa: BLE001
        return False
    return True


@pytest.fixture()
def _skip_if_no_server():
    if not httpx.get("http://localhost:11434/api/tags", timeout=2.0).is_success:
        pytest.skip("Ollama is not running on localhost:11434")


async def test_real_health_check_and_completion(_skip_if_no_server) -> None:
    provider = OllamaProvider(model_name="llama3.1:8b")

    healthy = await provider.health_check()
    if not healthy:
        pytest.skip("Ollama runs but llama3.1:8b is not installed locally")

    result = await provider.chat_completion([ChatMessage(role="user", content="Say 'ok'.")])

    assert result.message.role == "assistant"
    assert result.total_tokens > 0
    # Local models may ignore temperature=0; only structure is asserted here.
