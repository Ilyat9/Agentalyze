"""Provider factory: providers.yaml -> named, retry-wrapped Provider instances.

The config file lists an arbitrary number of *named* providers; secrets never
live in it — only the name of the environment variable holding the API key
(``api_key_env_var``), read at load time. See ``providers.example.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentalyze.providers.base import Provider, ProviderConfigError
from agentalyze.providers.ollama import DEFAULT_BASE_URL as OLLAMA_DEFAULT_BASE_URL
from agentalyze.providers.ollama import OllamaProvider
from agentalyze.providers.openai_compatible import OpenAICompatibleProvider
from agentalyze.providers.retry import RetryingProvider, RetryPolicy


class RetryConfig(BaseModel):
    """Optional per-provider overrides of :class:`RetryPolicy` defaults."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1)
    initial_wait_seconds: float = Field(default=1.0, ge=0)
    multiplier: float = Field(default=2.0, gt=1)
    max_wait_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=1.0, ge=0)


class ProviderConfigEntry(BaseModel):
    """One entry of ``providers.yaml``.

    ``api_key_env_var`` names an environment variable holding the actual key;
    the YAML file itself must stay free of secrets so it is safe to commit.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Unique human-readable provider id.")
    kind: Literal["openai_compatible", "ollama"]
    base_url: str | None = None
    api_key_env_var: str | None = None
    model_name: str = Field(min_length=1)
    timeout_seconds: float = Field(default=120.0, gt=0)
    health_check_timeout_seconds: float = Field(default=5.0, gt=0)
    retry: RetryConfig = Field(default_factory=RetryConfig)


def _read_config(config_path: Path) -> list[ProviderConfigEntry]:
    if not config_path.is_file():
        msg = f"providers config file not found: {config_path}"
        raise ProviderConfigError(msg)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in providers config {config_path}: {exc}"
        raise ProviderConfigError(msg) from exc
    if isinstance(raw, dict):
        raw = raw.get("providers")
    if not isinstance(raw, list):
        msg = (
            f"providers config {config_path} must be a list of provider entries "
            "(or a mapping with a 'providers' key)"
        )
        raise ProviderConfigError(msg)

    entries: list[ProviderConfigEntry] = []
    seen_names: set[str] = set()
    for index, item in enumerate(raw):
        try:
            entry = ProviderConfigEntry.model_validate(item)
        except Exception as exc:
            name_hint = item.get("name") if isinstance(item, dict) else None
            where = f"entry #{index} ({name_hint!r})" if name_hint else f"entry #{index}"
            msg = f"invalid provider config {where} in {config_path}: {exc}"
            raise ProviderConfigError(msg) from exc
        if entry.name in seen_names:
            msg = f"duplicate provider name {entry.name!r} in {config_path}"
            raise ProviderConfigError(msg)
        seen_names.add(entry.name)
        entries.append(entry)
    return entries


def _resolve_api_key(entry: ProviderConfigEntry, config_path: Path) -> str:
    if entry.kind == "ollama":
        # Local servers don't check keys; only read one when explicitly given.
        if entry.api_key_env_var is None:
            return "ollama"
        env_var = entry.api_key_env_var
    else:
        if entry.api_key_env_var is None:
            msg = (
                f"provider '{entry.name}' ({entry.kind}) in {config_path}: "
                "'api_key_env_var' is required for openai_compatible providers"
            )
            raise ProviderConfigError(msg)
        env_var = entry.api_key_env_var

    value = os.environ.get(env_var)
    if not value:
        msg = f"Provider '{entry.name}' requires environment variable '{env_var}', which is not set"
        raise ProviderConfigError(msg)
    return value


def _build_inner(entry: ProviderConfigEntry, config_path: Path) -> Provider:
    api_key = _resolve_api_key(entry, config_path)
    if entry.kind == "ollama":
        return OllamaProvider(
            name=entry.name,
            model_name=entry.model_name,
            timeout_seconds=entry.timeout_seconds,
            health_check_timeout_seconds=entry.health_check_timeout_seconds,
            base_url=entry.base_url or OLLAMA_DEFAULT_BASE_URL,
            api_key=api_key,
        )
    if entry.base_url is None:
        msg = f"provider '{entry.name}' ({entry.kind}): 'base_url' is required"
        raise ProviderConfigError(msg)
    return OpenAICompatibleProvider(
        name=entry.name,
        model_name=entry.model_name,
        timeout_seconds=entry.timeout_seconds,
        health_check_timeout_seconds=entry.health_check_timeout_seconds,
        base_url=entry.base_url,
        api_key=api_key,
    )


def load_providers(config_path: Path) -> dict[str, Provider]:
    """Load named providers from a YAML config, each wrapped in RetryingProvider.

    Returns ``{provider_name: Provider}``. Raises
    :class:`agentalyze.providers.base.ProviderConfigError` with a clear,
    actionable message for every misconfiguration case.
    """
    providers: dict[str, Provider] = {}
    for entry in _read_config(config_path):
        inner = _build_inner(entry, config_path)
        policy = RetryPolicy(
            max_attempts=entry.retry.max_attempts,
            initial_wait_seconds=entry.retry.initial_wait_seconds,
            multiplier=entry.retry.multiplier,
            max_wait_seconds=entry.retry.max_wait_seconds,
            jitter_seconds=entry.retry.jitter_seconds,
        )
        providers[entry.name] = RetryingProvider(inner, policy=policy)
    return providers

