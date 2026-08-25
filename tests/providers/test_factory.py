"""Tests for the provider factory (config parsing, env-var handling, wiring)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentalyze.providers.base import ProviderConfigError
from agentalyze.providers.factory import load_providers
from agentalyze.providers.ollama import OllamaProvider
from agentalyze.providers.openai_compatible import OpenAICompatibleProvider
from agentalyze.providers.retry import RetryingProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestExampleConfig:
    def test_repository_example_config_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        providers = load_providers(PROJECT_ROOT / "providers.example.yaml")

        assert set(providers) == {"gpt-4o-mini-via-openrouter", "llama31-8b-local"}
        for provider in providers.values():
            assert isinstance(provider, RetryingProvider)

    def test_openai_compatible_entry_builds_correct_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_KEY", "secret-value")
        config = tmp_path / "providers.yaml"
        config.write_text(
            yaml.safe_dump(
                [
                    {
                        "name": "gpt-test",
                        "kind": "openai_compatible",
                        "base_url": "http://api.test/v1",
                        "api_key_env_var": "TEST_KEY",
                        "model_name": "gpt-4o-mini",
                    }
                ]
            ),
            encoding="utf-8",
        )

        providers = load_providers(config)

        wrapped = providers["gpt-test"]
        assert isinstance(wrapped, RetryingProvider)
        assert isinstance(wrapped._inner, OpenAICompatibleProvider)
        assert wrapped.name == "gpt-test"

    def test_ollama_entry_builds_correct_type_with_defaults(self, tmp_path: Path) -> None:
        config = tmp_path / "providers.yaml"
        config.write_text(
            yaml.safe_dump([{"name": "local-llama", "kind": "ollama", "model_name": "llama3.1"}]),
            encoding="utf-8",
        )

        providers = load_providers(config)

        inner = providers["local-llama"]._inner  # type: ignore[union-attr]
        assert isinstance(inner, OllamaProvider)


class TestMissingEnvVar:
    def test_missing_env_var_raises_clear_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_KEY_VAR", raising=False)
        config = tmp_path / "providers.yaml"
        config.write_text(
            yaml.safe_dump(
                [
                    {
                        "name": "broken-provider",
                        "kind": "openai_compatible",
                        "base_url": "http://api.test/v1",
                        "api_key_env_var": "MISSING_KEY_VAR",
                        "model_name": "gpt-4o-mini",
                    }
                ]
            ),
            encoding="utf-8",
        )

        with pytest.raises(ProviderConfigError) as exc_info:
            load_providers(config)

        message = str(exc_info.value)
        assert "broken-provider" in message
        assert "MISSING_KEY_VAR" in message
        assert "not set" in message


class TestConfigValidation:
    def _write(self, tmp_path: Path, payload: object) -> Path:
        config = tmp_path / "providers.yaml"
        config.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return config

    def test_missing_file_raises_provider_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderConfigError, match="not found"):
            load_providers(tmp_path / "nope.yaml")

    def test_duplicate_names_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_KEY", "v")
        entry = {
            "name": "dup",
            "kind": "ollama",
            "model_name": "m",
        }
        with pytest.raises(ProviderConfigError, match="duplicate"):
            load_providers(self._write(tmp_path, [entry, entry]))

    def test_unknown_kind_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderConfigError):
            load_providers(
                self._write(tmp_path, [{"name": "x", "kind": "carrier_pigeon", "model_name": "m"}])
            )

    def test_unknown_fields_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ProviderConfigError):
            load_providers(
                self._write(tmp_path, [{"name": "x", "kind": "ollama", "model_name": "m", "hax": 1}])
            )

    def test_mapping_document_with_providers_key_is_accepted(
        self, tmp_path: Path
    ) -> None:
        config = tmp_path / "providers.yaml"
        config.write_text(
            yaml.safe_dump({"providers": [{"name": "l", "kind": "ollama", "model_name": "m"}]}),
            encoding="utf-8",
        )

        providers = load_providers(config)

        assert set(providers) == {"l"}

    def test_empty_list_is_valid_but_yields_nothing(self, tmp_path: Path) -> None:
        assert load_providers(self._write(tmp_path, [])) == {}
