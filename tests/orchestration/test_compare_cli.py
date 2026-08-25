"""`agentbench compare` CLI tests that don't need any real model.

The critical automation-safety property: a provider failing its health
check aborts the command BEFORE the suite run starts — no interactive
prompt, no partially-started run.
"""

from __future__ import annotations

import pytest

from agentalyze.runner.cli import main

DEAD_PROVIDER_YAML = """
providers:
  - name: dead-local
    kind: ollama
    base_url: http://127.0.0.1:9   # port 9: connection refused immediately
    model_name: no-such-model
    health_check_timeout_seconds: 2
"""


class TestCompareHealthCheckGate:
    def test_dead_provider_aborts_before_anything_runs(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        config_path = tmp_path / "providers.yaml"
        config_path.write_text(DEAD_PROVIDER_YAML, encoding="utf-8")
        results_dir = tmp_path / "results"
        monkeypatch.setenv("AGENTALYZE_PROVIDERS_CONFIG_PATH", str(config_path))
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main(["compare", "--providers", "dead-local",
                     "--category", "navigation"])

        assert code == 2
        captured = capsys.readouterr()
        assert "failed health check" in captured.err
        assert "refusing to start" in captured.err
        assert not results_dir.exists()  # nothing was created, nothing was run

    def test_unknown_provider_is_a_clean_error(self, tmp_path, monkeypatch, capsys) -> None:
        config_path = tmp_path / "providers.yaml"
        config_path.write_text(DEAD_PROVIDER_YAML, encoding="utf-8")
        monkeypatch.setenv("AGENTALYZE_PROVIDERS_CONFIG_PATH", str(config_path))
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(tmp_path / "results"))

        code = main(["compare", "--providers", "not-configured", "--all-tasks"])

        assert code == 2
        assert "unknown provider(s)" in capsys.readouterr().err

    def test_selection_flags_are_mutually_exclusive(self, capsys) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["compare", "--providers", "x", "--all-tasks",
                  "--category", "navigation"])
        assert excinfo.value.code == 2
