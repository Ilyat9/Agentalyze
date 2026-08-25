"""Command-line entry point: ``agentbench run --task <id> --provider <name>``.

CLI toolkit choice: argparse from the standard library. The project carries
neither click nor typer as dependencies and this is the only command-line
surface, so adding one just for a couple of flags would be unjustified.
Output here is a short human-readable summary; the machine-readable artifact
is the trace JSON on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agentalyze.config import Settings
from agentalyze.providers import load_providers
from agentalyze.runner import run_task
from agentalyze.tasks.registry import TASKS, TASKS_BY_ID


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbench",
        description="Agentalyze benchmark harness (see ROADMAP.md for phases).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one task with one provider.")
    run_parser.add_argument("--task", required=True, help="Task id, e.g. form-fill-basic-01.")
    run_parser.add_argument(
        "--provider",
        required=True,
        help="Provider name from providers.yaml, e.g. gpt-4o-mini-via-openrouter.",
    )
    run_parser.add_argument(
        "--providers-config",
        default=None,
        help="Override AGENTALYZE_PROVIDERS_CONFIG_PATH for this invocation.",
    )
    run_parser.add_argument(
        "--results-dir",
        default=None,
        help="Override AGENTALYZE_RESULTS_DIR for this invocation.",
    )
    run_parser.add_argument(
        "--fixtures-dir",
        default=None,
        help="Override AGENTALYZE_FIXTURES_DIR for this invocation.",
    )

    subparsers.add_parser("tasks", help="List registered tasks and exit.")

    # Phase 5: comparison commands live in agentalyze.orchestration.cli but
    # are registered HERE so `agentbench` stays the single entry point.
    from agentalyze.orchestration.cli import register_parsers

    register_parsers(subparsers)
    return parser


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """Rebuild settings with CLI overrides so types are re-validated by pydantic.

    Tolerates subcommands that don't define every override flag (e.g.
    `inspect` has no --fixtures-dir): missing attributes are simply absent
    overrides.
    """
    overrides = {}
    if getattr(args, "providers_config", None):
        overrides["providers_config_path"] = args.providers_config
    if getattr(args, "results_dir", None):
        overrides["results_dir"] = args.results_dir
    if getattr(args, "fixtures_dir", None):
        overrides["fixtures_dir"] = args.fixtures_dir
    if not overrides:
        return settings
    return Settings(**{**settings.model_dump(), **overrides})


def _print_task_list() -> None:
    print(f"{len(TASKS)} registered tasks:")
    for task in TASKS:
        print(f"  {task.id:<28} {task.category.value:<15} {task.difficulty:<6} {task.title}")


def _print_summary(trace, task_title: str) -> None:  # type: ignore[no-untyped-def]
    cost = "N/A" if trace.total_cost_usd is None else f"${trace.total_cost_usd:.4f}"
    verifier = "-" if trace.verifier_result is None else trace.verifier_result.reason
    print()
    print("=" * 62)
    print(f"Task:       {trace.task_id} ({task_title})")
    print(f"Provider:   {trace.provider_name}")
    print(f"Outcome:    {trace.outcome.value}")
    print(f"Steps:      {len(trace.steps)}")
    print(
        f"Tokens:     prompt={trace.total_prompt_tokens} "
        f"completion={trace.total_completion_tokens} cost={cost}"
    )
    print(f"Verifier:   {verifier}")
    print(f"Wall time:  {trace.wall_clock_seconds:.1f}s")
    print(f"Trace:      {trace.run_id}/trace.json")
    print("=" * 62)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = Settings()

    if args.command == "tasks":
        _print_task_list()
        return 0

    # Phase 5 commands handle their own provider loading / health checks.
    if args.command == "compare":
        from agentalyze.orchestration.cli import cmd_compare

        return cmd_compare(args, _apply_overrides(settings, args))

    if args.command == "inspect":
        from agentalyze.orchestration.cli import cmd_inspect

        return cmd_inspect(args, _apply_overrides(settings, args))

    settings = _apply_overrides(settings, args)
    task = TASKS_BY_ID.get(args.task)
    if task is None:
        print(f"error: unknown task {args.task!r}; run `agentbench tasks` to list ids.",
              file=sys.stderr)
        return 2

    try:
        providers = load_providers(settings.providers_config_path)
    except Exception as exc:  # noqa: BLE001 - ProviderConfigError has an actionable message
        print(f"error: cannot load providers config: {exc}", file=sys.stderr)
        return 2

    provider = providers.get(args.provider)
    if provider is None:
        available = ", ".join(sorted(providers)) or "<none>"
        print(
            f"error: unknown provider {args.provider!r}. Configured: {available}",
            file=sys.stderr,
        )
        return 2

    trace = asyncio.run(run_task(task, provider, settings))
    _print_summary(trace, task.title)
    return 0 if trace.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
