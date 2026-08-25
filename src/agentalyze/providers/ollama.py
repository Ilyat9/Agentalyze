"""Ollama provider: a thin wrapper over the OpenAI-compatible implementation.

Modern Ollama versions expose an OpenAI-compatible ``/v1/chat/completions``
endpoint, including function/tool calling in the same JSON shape as the
OpenAI API. Verified against Ollama's documented OpenAI compatibility layer
(https://github.com/ollama/ollama/blob/main/docs/openai.md): request and
response formats for messages and tools match, so duplicating any mapping
logic here would be an architectural mistake. ``OllamaProvider`` therefore
only adjusts what genuinely differs:

* sensible local defaults (``base_url``, dummy ``api_key``);
* ``health_check`` hits ``GET /api/tags`` instead of spending tokens on a
  generation, and additionally verifies that the configured model is actually
  available locally — "server alive" alone is not healthy enough for evals.
"""

from __future__ import annotations

import httpx

from agentalyze.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_API_KEY = "ollama"  # placeholder; Ollama does not check keys locally


def _root_from_base_url(base_url: str) -> str:
    """Derive the server root (for ``/api/tags``) from a ``.../v1`` base URL."""
    root = base_url.rstrip("/")
    suffixes = ("/v1", "/v")
    for suffix in suffixes:
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root


class OllamaProvider(OpenAICompatibleProvider):
    """Local Ollama via its OpenAI-compatible endpoint. See module docstring."""

    def __init__(
        self,
        *,
        model_name: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = DEFAULT_API_KEY,
        name: str | None = None,
        timeout_seconds: float = 300.0,  # local generation can be slow
        health_check_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            name=name or f"{model_name}-local",
            timeout_seconds=timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
        )
        self._base_url = base_url

    async def health_check(self) -> bool:
        """True iff the server answers ``/api/tags`` AND lists ``model_name``."""
        url = f"{_root_from_base_url(self._base_url)}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=self._health_check_timeout_seconds) as client:
                response = await client.get(url)
            response.raise_for_status()
            tags = response.json()
            installed = tags.get("models") or [] if isinstance(tags, dict) else []
        except Exception:  # noqa: BLE001 - health check must not crash callers
            return False
        wanted = self._model_name
        for entry in installed:
            name = str(entry.get("name", ""))
            # Ollama reports e.g. "llama3.1:8b"; also accept bare "llama3.1".
            if name == wanted or name.split(":")[0] == wanted:
                return True
        return False
