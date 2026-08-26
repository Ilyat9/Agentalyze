"""Optional per-task gate configuration for ``agentalyze regression-check``.

Loaded from ``regression.yaml`` (path overridable via
``AGENTALYZE_REGRESSION_CONFIG_PATH`` or ``--regression-config``). The file is
entirely OPTIONAL: when it is absent, the regression gate behaves exactly as
it did before this configuration existed — every SUCCESS -> FAILURE_*
transition counts as a regression.

Implemented scope — an ALLOWLIST::

    excluded_from_gate:
      - task_id: err-flaky-widget-03     # required
        reason: verifier flakes historically   # recommended, free text

Tasks on the list still appear in the diff output (marked
"excluded from gate"), but they do NOT count towards ``regressed_count``
and therefore cannot turn the CI gate red by themselves.

Deliberately NOT implemented — a sliding-window threshold ("count as a
regression only after 2+ failures out of the last N runs"): the storage layer
persists complete suite runs and compares them PAIRWISE (baseline vs new);
there is no per-task history of recent outcomes to compute "last N runs"
over. Adding it would require a new history index across stored run ids — a
storage-format change that far exceeds this knob's value while the suite is
small and noisy. Revisit if per-run history storage ever lands.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegressionConfigError(Exception):
    """Raised for unreadable or semantically invalid regression.yaml files."""


class ExcludedTask(BaseModel):
    """One allowlist entry: a task id plus a human explanation."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    reason: str = ""

    @field_validator("task_id", "reason", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RegressionConfig(BaseModel):
    """The parsed content of an optional regression.yaml."""

    model_config = ConfigDict(extra="forbid")

    excluded_from_gate: list[ExcludedTask] = Field(default_factory=list)

    def excluded_task_ids(self) -> frozenset[str]:
        ids = [entry.task_id for entry in self.excluded_from_gate]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            msg = f"duplicate task_id(s) in excluded_from_gate: {duplicates}"
            raise RegressionConfigError(msg)
        return frozenset(ids)


def load_regression_config(config_path: Path) -> RegressionConfig:
    """Load regression.yaml; a missing file yields an EMPTY config (not None).

    The empty-config-instead-of-None choice keeps callers symmetric: there is
    no "unconfigured" code path, the gate simply has no exclusions.
    Raises :class:`RegressionConfigError` with an actionable message on any
    malformed content.
    """
    if not config_path.is_file():
        return RegressionConfig()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML in regression config {config_path}: {exc}"
        raise RegressionConfigError(msg) from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = (
            f"regression config {config_path} must be a mapping with an "
            "'excluded_from_gate' key"
        )
        raise RegressionConfigError(msg)

    # Tolerate bare-string entries ("- err-flaky-widget-03") alongside full
    # mappings ("- task_id: ... / reason: ...").
    entries = raw.get("excluded_from_gate", [])
    normalized: list[dict[str, str] | str] = []
    for entry in entries:
        if isinstance(entry, str):
            normalized.append({"task_id": entry})
        else:
            normalized.append(entry)

    try:
        return RegressionConfig.model_validate({"excluded_from_gate": normalized})
    except Exception as exc:
        msg = f"invalid regression config {config_path}: {exc}"
        raise RegressionConfigError(msg) from exc
