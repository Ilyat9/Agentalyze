"""Validate the Phase 6 GitHub Actions workflow TEMPLATE.

The `.example` file is never executed by GitHub (deliberate), but it must
still be syntactically valid YAML: the whole activation procedure is
"rename and add secrets", so a typo here would surface only AFTER a user
committed to using it. This test catches syntax breakage early.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parents[2] / ".github/workflows/regression-check.yml.example"
)


def test_template_exists_and_is_valid_yaml() -> None:
    assert WORKFLOW_TEMPLATE.exists(), "the .yml.example template went missing"

    data = yaml.safe_load(WORKFLOW_TEMPLATE.read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    # NOTE: PyYAML implements YAML 1.1, where the bare key `on` parses as the
    # boolean True. Either spelling proves the trigger block parsed fine.
    assert True in data or "on" in data


def test_template_declares_the_regression_job() -> None:
    data = yaml.safe_load(WORKFLOW_TEMPLATE.read_text(encoding="utf-8"))

    assert "jobs" in data
    assert list(data["jobs"]) == ["regression-check"]
    steps = data["jobs"]["regression-check"]["steps"]

    def strip_yaml_block_comments(text: str) -> str:
        return "\n".join(line.split("#")[0] for line in text.splitlines())

    run_commands = [strip_yaml_block_comments(step.get("run", "")) for step in steps]
    assert any("agentalyze compare" in cmd for cmd in run_commands)
    gate_cmds = [cmd for cmd in run_commands if "agentalyze regression-check" in cmd]
    assert gate_cmds, "the template must contain a regression-check gate step"
    # The gate must be a real gate in CI: --allow-regressions belongs only to
    # local manual use, so it must not appear in any executed command line.
    assert all("--allow-regressions" not in cmd for cmd in gate_cmds)


if __name__ == "__main__":  # pragma: no cover - manual script usage
    test_template_exists_and_is_valid_yaml()
    test_template_declares_the_regression_job()
    print(f"OK: {WORKFLOW_TEMPLATE.name} is valid YAML with the expected job.")
