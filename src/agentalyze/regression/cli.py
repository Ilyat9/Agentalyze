"""CLI handlers for Phase 6: ``agentalyze regression-check`` / ``set-baseline``.

Not a second entry point: the parsers are registered by
``agentalyze.runner.cli`` (the single ``agentalyze`` command), same pattern
as the Phase 5 orchestration commands.

CI-gate contract (the reason this phase exists)::

    exit 0 — no regressions (or --allow-regressions was passed)
    exit 1 — regressed_count > 0 and regressions are not allowed
    exit 2 — usage/configuration problem (unknown run id, no baseline set)

The numeric codes are load-bearing: a CI step fails exactly when the gate
returns 1. They are pinned by tests/regression/test_cli_exit_codes.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentalyze.config import Settings
from agentalyze.regression.config import RegressionConfigError, load_regression_config
from agentalyze.regression.diff import TaskDiff, TaskDiffStatus, compute_regression
from agentalyze.regression.storage import (
    AutoBaselineNotFoundError,
    BaselineNotSetError,
    SuiteRunNotFoundError,
    find_last_clean_baseline,
    get_current_baseline,
    load_saved_suite_run,
    record_gate_outcome,
    require_current_baseline,
    save_regression_report,
    set_baseline,
)
from agentalyze.runner.trace import RunOutcome

_SECTION_TITLES = {
    TaskDiffStatus.REGRESSED: "REGRESSED",
    TaskDiffStatus.FIXED: "FIXED",
    TaskDiffStatus.NEWLY_ADDED: "NEWLY ADDED (task appeared after the baseline)",
    TaskDiffStatus.REMOVED: "REMOVED (task absent from the new run)",
}


def _outcome_label(outcome: RunOutcome | None) -> str:
    return outcome.value if outcome is not None else "<absent>"


def _print_diff_line(diff: TaskDiff, settings: Settings) -> None:
    line = (
        f"  - task={diff.task_id}  provider={diff.provider_name}  "
        f"{_outcome_label(diff.baseline_outcome)} -> {_outcome_label(diff.new_outcome)}"
    )
    if diff.cost_delta_usd is not None:
        line += f"  cost_delta=${diff.cost_delta_usd:+.4f}"
    if diff.steps_delta is not None:
        line += f"  steps_delta={diff.steps_delta:+d}"
    if diff.gate_excluded:
        # The pair stays fully visible; only its gate impact is waived.
        line += "  [excluded from gate]"
    print(line)
    # The trace pointer is what a developer follows right after seeing the
    # regression line — one concrete artifact per side of the comparison.
    for run_id in (diff.baseline_run_id, diff.new_run_id):
        if run_id is not None:
            trace_path = Path(settings.results_dir) / run_id / "trace.json"
            print(f"      trace: {trace_path}")


def cmd_regression_check(args: argparse.Namespace, settings: Settings) -> int:
    """`agentalyze regression-check`: diff two runs; exit 1 on regressions."""
    baseline_arg = (args.baseline or "").strip()
    try:
        if baseline_arg.lower() == "auto":
            # OPT-IN convenience: newest run whose recorded gate outcome was
            # clean. NOT a replacement for an explicit baseline in CI — see
            # README ("Automatic baseline").
            try:
                baseline_id = find_last_clean_baseline(settings.results_dir)
                print(f"Auto baseline resolved to suite run {baseline_id} "
                      "(newest clean gated run).")
            except AutoBaselineNotFoundError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        elif baseline_arg:
            baseline_id = baseline_arg
        else:
            baseline_id = require_current_baseline(settings.results_dir)
    except BaselineNotSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        baseline = load_saved_suite_run(settings.results_dir, baseline_id)
        new = load_saved_suite_run(settings.results_dir, args.new)
    except SuiteRunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config_path = getattr(args, "regression_config", None)
    regression_config = load_regression_config(Path(config_path or settings.regression_config_path))
    try:
        excluded_ids = regression_config.excluded_task_ids()
    except RegressionConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = compute_regression(baseline, new, excluded_task_ids=excluded_ids)
    report_path = save_regression_report(report, settings.results_dir)
    # Record the gate outcome for --baseline auto: the FACT of this comparison
    # (clean or not), regardless of --allow-regressions. Never moves the
    # pointer by itself.
    record_gate_outcome(
        settings.results_dir, report.new_suite_run_id,
        was_clean=report.regressed_count == 0,
    )

    # ------------------------ human-readable summary -------------------------
    print("=" * 70)
    print(f"Regression check: baseline={report.baseline_suite_run_id}")
    print(f"                  new      ={report.new_suite_run_id}")
    compared = sum(s.compared_pairs for s in report.provider_summary.values())
    print(f"Compared {compared} pair(s) across "
          f"{len(report.provider_summary)} common provider(s).")
    print(f"Regressed: {report.regressed_count}   Fixed: {report.fixed_count}   "
          f"Net change: {report.net_change:+d}")
    excluded_regressions = [
        d for d in report.diffs
        if d.status is TaskDiffStatus.REGRESSED and d.gate_excluded
    ]
    if excluded_regressions:
        print(
            f"NOTE {len(excluded_regressions)} regressed pair(s) on the "
            "regression.yaml allowlist are shown below but excluded from the "
            "gate count."
        )
    if report.providers_only_in_baseline:
        print("NOTE providers only in BASELINE (not compared): "
              + ", ".join(report.providers_only_in_baseline))
    if report.providers_only_in_new:
        print("NOTE providers only in NEW run (not compared): "
              + ", ".join(report.providers_only_in_new))

    interesting = [
        d for d in report.diffs
        if d.status not in (TaskDiffStatus.STILL_PASSING, TaskDiffStatus.STILL_FAILING)
    ]
    if not interesting:
        print("\nNo status changes: every compared pair kept its outcome.")
    else:
        by_status: dict[TaskDiffStatus, list[TaskDiff]] = {}
        for diff in interesting:
            by_status.setdefault(diff.status, []).append(diff)
        for status in sorted(by_status, key=lambda s: s.value):
            print(f"\n{_SECTION_TITLES[status]} ({len(by_status[status])}):")
            for diff in by_status[status]:
                _print_diff_line(diff, settings)

    if report.provider_summary:
        print("\nProvider breakdown:")
        for name, summary in report.provider_summary.items():
            print(
                f"  {name}: compared={summary.compared_pairs} "
                f"regressed={summary.regressed_count} fixed={summary.fixed_count} "
                f"still_passing={summary.still_passing_count} "
                f"still_failing={summary.still_failing_count} "
                f"added={summary.newly_added_count} removed={summary.removed_count}"
            )

    print("=" * 70)
    print(f"Full report saved: {report_path}")

    # --------------------------- the CI gate itself --------------------------
    if report.regressed_count > 0 and not args.allow_regressions:
        print(
            f"RESULT: FAIL — {report.regressed_count} regression(s) vs baseline. "
            "(Re-run with --allow-regressions to inspect without failing.)"
        )
        return 1
    if report.regressed_count > 0:
        print(f"RESULT: {report.regressed_count} regression(s) found, but "
              "--allow-regressions is set: reporting only, exiting 0.")
    else:
        print("RESULT: OK — no regressions against the baseline.")
    return 0


def cmd_set_baseline(args: argparse.Namespace, settings: Settings) -> int:
    """`agentalyze set-baseline`: mark a finished run as the comparison base."""
    try:
        result = load_saved_suite_run(settings.results_dir, args.suite_run)
    except SuiteRunNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    pointer = set_baseline(settings.results_dir, result.suite_run_id)
    assert get_current_baseline(settings.results_dir) == result.suite_run_id
    print(f"Baseline set to suite run {result.suite_run_id} "
          f"({len(result.traces)} trace(s)).")
    print(f"Pointer file: {pointer}")
    print("Future `regression-check --new <id>` calls will compare against it.")
    return 0


def register_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Attach `regression-check` and `set-baseline` to `agentalyze`."""
    check_parser = subparsers.add_parser(
        "regression-check",
        help="Compare a new suite run against a baseline; exit 1 on regressions.",
    )
    check_parser.add_argument(
        "--baseline", default=None,
        help=("Baseline suite_run_id. Omit to use the baseline marked via "
              "`agentalyze set-baseline`. The special value 'auto' picks the "
              "newest run whose last regression-check was clean (opt-in "
              "convenience; explicit baselines remain the recommended choice "
              "for CI gates)."),
    )
    check_parser.add_argument(
        "--new", required=True,
        help="suite_run_id of the just-finished run to compare.",
    )
    check_parser.add_argument(
        "--allow-regressions", action="store_true",
        help="Report regressions but always exit 0 (for manual inspection).",
    )
    check_parser.add_argument(
        "--regression-config", default=None,
        help=("Path to an optional regression.yaml (allowlist of noisy tasks "
              "excluded from the gate). Defaults to ./regression.yaml; a "
              "missing file means no exclusions."),
    )
    check_parser.add_argument("--providers-config", default=None)
    check_parser.add_argument("--results-dir", default=None)

    baseline_parser = subparsers.add_parser(
        "set-baseline",
        help="Mark an existing suite run as the current regression baseline.",
    )
    baseline_parser.add_argument(
        "--suite-run", required=True,
        help="suite_run_id of a finished `compare` run.",
    )
    baseline_parser.add_argument("--results-dir", default=None)
