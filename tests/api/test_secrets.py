"""Secret resolution: env default preserved, Vault optional with safe fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from agentalyze.config import Settings
from agentalyze.secrets import SecretResolutionError, resolve_secret


class TestEnvDefault:
    def test_env_fallback_when_no_vault(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_SECRET_X", raising=False)
        settings = Settings(vault_addr="")
        assert resolve_secret("MY_SECRET_X", settings) is None
        monkeypatch.setenv("MY_SECRET_X", "value-from-env")
        assert resolve_secret("MY_SECRET_X", settings) == "value-from-env"


class TestVault:
    def _settings(self, addr: str) -> Settings:
        return Settings(vault_addr=addr, vault_kv_mount="secret")

    @respx.mock
    def test_vault_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_SECRET_X", raising=False)
        respx.get("https://vault.example/v1/secret/data/MY_SECRET_X").respond(
            json={"data": {"data": {"value": "from-vault"}}}
        )
        value = resolve_secret("MY_SECRET_X", self._settings("https://vault.example"))
        assert value == "from-vault"

    @respx.mock
    def test_missing_in_vault_falls_back_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_SECRET_X", "env-value")
        respx.get("https://vault.example/v1/secret/data/MY_SECRET_X").respond(status_code=404)
        value = resolve_secret("MY_SECRET_X", self._settings("https://vault.example"))
        assert value == "env-value"

    @respx.mock
    def test_unreachable_vault_degrades_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The deliberate contract: a dead Vault must not take the service down.
        monkeypatch.setenv("MY_SECRET_X", "env-value")
        respx.get("https://vault.example/v1/secret/data/MY_SECRET_X").mock(
            side_effect=httpx.ConnectError("boom")
        )
        value = resolve_secret("MY_SECRET_X", self._settings("https://vault.example"))
        assert value == "env-value"

    @respx.mock
    def test_hard_vault_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_SECRET_X", raising=False)
        respx.get("https://vault.example/v1/secret/data/MY_SECRET_X").respond(status_code=403)
        with pytest.raises(SecretResolutionError):
            resolve_secret("MY_SECRET_X", self._settings("https://vault.example"))


class TestFactoryIntegration:
    def test_load_providers_uses_vault_value(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
    ) -> None:
        import yaml

        from agentalyze.providers.factory import load_providers

        config = tmp_path / "providers.yaml"
        config.write_text(
            yaml.safe_dump(
                {"providers": [{
                    "name": "cloud",
                    "kind": "openai_compatible",
                    "base_url": "https://api.example/v1",
                    "api_key_env_var": "CLOUD_KEY",
                    "model_name": "m",
                }]}
            ),
            encoding="utf-8",
        )
        monkeypatch.delenv("CLOUD_KEY", raising=False)

        @respx.mock
        def _run() -> None:
            respx.get("https://vault.example/v1/secret/data/CLOUD_KEY").respond(
                json={"data": {"data": {"value": "vk"}}}
            )
            providers = load_providers(
                config,
                Settings(vault_addr="https://vault.example"),
            )
            assert providers["cloud"].name == "cloud"

        _run()
