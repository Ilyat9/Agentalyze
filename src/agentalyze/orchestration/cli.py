"""CLI handlers for Phase 5: ``agentbench compare`` / ``agentbench inspect``.

These are NOT a second command-line tool: the parsers are registered by
``agentalyze.runner.cli`` (the single ``agentbench`` entry point from
Phase 3) as additional subcommands; this module only holds their logic.

Design note on health checks: ``compare`` probes every selected provider
BEFORE starting the suite run. A provider failing its check aborts the
command with an explicit error — never an interactive prompt, because this
CLI is also used from automation where ``input()`` would deadlock.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agentalyze.analysis.failure_taxonomy import FailureTag, classify_failure
from agentalyze.config import Settings
from agentalyze.orchestration.report import generate_report
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
    load_suite_run,
    run_suite,
)
from agentalyze.providers import load_providers
from agentalyze.providers.base import Provider
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.models import TaskCategory


def _split_csv(raw: str) -> list[str]:
    """'a, b ,c' -> ['a', 'b', 'c'] (dedup, order kept)."""
    return list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


def _parse_categories(raw: str) -> list[TaskCategory]:
    categories: list[TaskCategory] = []
    unknown: list[str] = []
    for name in _split_csv(raw):
        try:
            category = TaskCategory(name.lower())
        except ValueError:
            unknown.append(name)
            continue
        if category not in categories:
            categories.append(category)
    if unknown:
        valid = ", ".join(category.value for category in TaskCategory)
        msg = f"unknown task category(ies) {unknown}; valid values: {valid}"
        raise ValueError(msg)
    if not categories:
        msg = "--category resolved to an empty list"
        raise ValueError(msg)
    return categories


async def _health_checks(providers: dict[str, Provider]) -> list[str]:
    """Run all health checks concurrently; returns names of unhealthy ones."""
    results = await asyncio.gather(
        *(provider.health_check() for provider in providers.values())
    )
    return [
        name for name, healthy in zip(providers, results, strict=True) if not healthy
    ]


def cmd_compare(args: argparse.Namespace, settings: Settings) -> int:
    """`agentbench compare`: health-check providers, run the suite, write report."""
    provider_names = _split_csv(args.providers)
    if not provider_names:
        print("error: --providers must name at least one configured provider",
              file=sys.stderr)
        return 2

    try:
        providers_all = load_providers(settings.providers_config_path)
    except Exception as exc:  # noqa: BLE001 - ProviderConfigError has an actionable message
        print(f"error: cannot load providers config: {exc}", file=sys.stderr)
        return 2

    unknown = [name for name in provider_names if name not in providers_all]
    if unknown:
        available = ", ".join(sorted(providers_all)) or "<none>"
        print(f"error: unknown provider(s) {unknown}. Configured: {available}",
              file=sys.stderr)
        return 2

    selected = {name: providers_all[name] for name in provider_names}

    # Fail BEFORE the long run, not halfway through it.
    print(f"Health-checking {len(selected)} provider(s): {', '.join(selected)}",
          flush=True)
    unhealthy = asyncio.run(_health_checks(selected))
    if unhealthy:
        for name in unhealthy:
            print(f"error: provider '{name}' failed health check and is unavailable.",
                  file=sys.stderr)
        print("Aborting: refusing to start a suite run with a dead provider "
              "(nothing was run).", file=sys.stderr)
        return 2

    try:
        config = SuiteRunConfig(
            task_ids=_split_csv(args.tasks) if args.tasks else None,
            provider_names=provider_names,
            category_filter=_parse_categories(args.category) if args.category else None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = asyncio.run(run_suite(config, selected, settings))
    report_path = generate_report(result, settings.results_dir)
    print()
    print("=" * 62)
    print(f"Suite run:  {result.suite_run_id}")
    print(f"Report:     {report_path}")
    print(f"Traces:     {len(result.traces)} individual run(s) under "
          f"{Path(settings.results_dir)}")
    print("Inspect:    agentbench inspect --suite-run " + result.suite_run_id)
    print("=" * 62)
    return 0


def _parse_tag(raw: str) -> FailureTag | None:
    try:
        return FailureTag(raw.lower().strip())
    except ValueError:
        valid = ", ".join(tag.value for tag in FailureTag)
        print(f"error: unknown failure tag {raw!r}; valid tags: {valid}", file=sys.stderr)
        return None


def _parse_outcome(raw: str) -> RunOutcome | None:
    try:
        return RunOutcome(raw.lower().strip())
    except ValueError:
        valid = ", ".join(outcome.value for outcome in RunOutcome)
        print(f"error: unknown outcome {raw!r}; valid outcomes: {valid}", file=sys.stderr)
        return None


def cmd_inspect(args: argparse.Namespace, settings: Settings) -> int:
    """`agentbench inspect`: find interesting traces of one suite run."""
    try:
        result = load_suite_run(settings.results_dir, args.suite_run)
    except FileNotFoundError:
        print(f"error: no suite run {args.suite_run!r} found under "
              f"{settings.results_dir}", file=sys.stderr)
        return 2

    tag_filter = _parse_tag(args.tag) if args.tag else None
    outcome_filter = _parse_outcome(args.outcome) if args.outcome else None
    if (args.tag and tag_filter is None) or (args.outcome and outcome_filter is None):
        return 2  # the reason was already printed by the parser helpers

    matches = [
        trace for trace in result.traces
        if (tag_filter is None or tag_filter in classify_failure(trace))
        and (outcome_filter is None or trace.outcome is outcome_filter)
    ]

    filters: list[str] = []
    if tag_filter is not None:
        filters.append(f"tag={tag_filter.value}")
    if outcome_filter is not None:
        filters.append(f"outcome={outcome_filter.value}")
    header = f"Suite run {result.suite_run_id}: {len(matches)}/{len(result.traces)} trace(s)"
    if filters:
        header += f" matching {' & '.join(filters)}"
    print(header)

    if not matches:
        print("Nothing matched. Try without filters, or another tag/outcome.")
        return 0

    for trace in matches:
        tags = ",".join(tag.value for tag in classify_failure(trace)) or "-"
        verifier = "-" if trace.verifier_result is None else (
            "ok" if trace.verifier_result.success else "failed"
        )
        print(
            f"- {trace.run_id}\n"
            f"    task={trace.task_id}  provider={trace.provider_name}  "
            f"steps={len(trace.steps)}\n"
            f"    outcome={trace.outcome.value}  verifier={verifier}  tags={tags}\n"
            f"    artifacts: {Path(settings.results_dir) / trace.run_id}/ "
            "(trace.json + screenshots/)"
        )
    return 0


def register_parsers(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Attach `compare` and `inspect` to the shared `agentbench` parser."""
    compare_parser = subparsers.add_parser(
        "compare",
        help="Run (selected) tasks across several providers and build a report.",
    )
    selection = compare_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all-tasks", action="store_true",
                           help="Run every registered task.")
    selection.add_argument("--category", default=None,
                           help="Comma-separated categories, e.g. form_fill,error_recovery.")
    selection.add_argument("--tasks", default=None,
                           help="Comma-separated concrete task ids.")
    compare_parser.add_argument(
        "--providers", required=True,
        help="Comma-separated provider names from providers.yaml.",
    )
    compare_parser.add_argument("--providers-config", default=None)
    compare_parser.add_argument("--results-dir", default=None)
    compare_parser.add_argument("--fixtures-dir", default=None)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="List traces of a finished suite run by failure tag or outcome.",
    )
    inspect_parser.add_argument("--suite-run", required=True,
                                help="suite_run_id of a finished `compare` run.")
    inspect_parser.add_argument("--tag", default=None,
                                help="FailureTag value, e.g. looping.")
    inspect_parser.add_argument("--outcome", default=None,
                                help="RunOutcome value, e.g. failure_verifier.")
    inspect_parser.add_argument("--providers-config", default=None)
    inspect_parser.add_argument("--results-dir", default=None)
