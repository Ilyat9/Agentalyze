"""Command-line entry point: ``agentalyze run --task <id> --provider <name>``.

CLI toolkit choice: argparse from the standard library. The project carries
neither click nor typer as dependencies, and this is the only command-line
surface, so adding one just for a couple of flags would be unjustified.
Output here is a short human-readable summary; the machine-readable artifact
is the trace JSON on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agentalyze.analysis.failure_taxonomy import FailureTag
from agentalyze.config import Settings
from agentalyze.providers import load_providers
from agentalyze.runner import run_task
from agentalyze.tasks.registry import TASKS, TASKS_BY_ID


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentalyze",
        description="Agentalyze benchmark harness (see docs/ROADMAP.md for project history).",
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
    run_parser.add_argument(
        "--agent-style",
        choices=["tool_calling", "code"],
        default="tool_calling",
        help=(
            "'tool_calling' (default): the structured tool-calling ReAct "
            "loop. 'code': the smolagents CodeAgent runner (requires "
            "pip install -e '.[code-agent]'); see "
            "agentalyze.runner.code_agent and docs/KNOWN_LIMITATIONS.md."
        ),
    )

    subparsers.add_parser("tasks", help="List registered tasks and exit.").add_argument(
        "--tag",
        default=None,
        metavar="FailureTag",
        choices=[tag.value for tag in FailureTag],
        help=(
            "Only show tasks whose expected_failure_modes include this "
            f"failure tag. Values: {', '.join(tag.value for tag in FailureTag)}."
        ),
    )

    # Service-mode commands (lazy imports keep pure-CLI installs FastAPI-free).
    serve_parser = subparsers.add_parser(
        "serve",
        help="Run the HTTP API server (service mode; requires pip install -e '.[api]').",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument(
        "--demo-mode",
        action="store_true",
        help=(
            "Expose the public BYOK demo endpoints (GET /demo, POST /demo/run; "
            "see docs/DEMO_DEPLOYMENT.md). OFF by default — equivalent to "
            "AGENTALYZE_DEMO_MODE_ENABLED=1."
        ),
    )

    key_parser = subparsers.add_parser(
        "create-api-key",
        help="Create a hashed Bearer API key for the HTTP API (requires [api]).",
    )
    key_parser.add_argument("--name", required=True, help="Human-readable client name.")

    revoke_key_parser = subparsers.add_parser(
        "revoke-api-key",
        help="Deactivate an API key by name (requires [api]).",
    )
    revoke_key_parser.add_argument("--name", required=True)

    # Phase 5: comparison commands live in agentalyze.orchestration.cli but
    # are registered HERE so `agentalyze` stays the single entry point.
    from agentalyze.orchestration.cli import register_parsers

    register_parsers(subparsers)

    # Phase 6: regression-gate commands, same registration pattern.
    from agentalyze.regression.cli import register_parsers as register_regression_parsers

    register_regression_parsers(subparsers)
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
    if getattr(args, "regression_config", None):
        overrides["regression_config_path"] = args.regression_config
    if not overrides:
        return settings
    return Settings(**{**settings.model_dump(), **overrides})


def _print_task_list(tag_filter: FailureTag | None = None) -> None:
    """Print the task index, optionally restricted to one failure-mode tag."""
    tasks = (
        [task for task in TASKS if tag_filter in task.expected_failure_modes]
        if tag_filter is not None
        else list(TASKS)
    )
    suffix = f" matching tag={tag_filter.value}" if tag_filter is not None else ""
    print(f"{len(tasks)} registered task(s){suffix}:")
    for task in tasks:
        print(f"  {task.id:<28} {task.category.value:<15} {task.difficulty:<6} {task.title}")
    if tag_filter is not None and not tasks:
        print(
            "(No task declares this failure mode yet; see expected_failure_modes "
            "in src/agentalyze/tasks/registry.py.)"
        )


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


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    """`agentalyze serve`: run the HTTP API (requires the [api] extra)."""
    try:
        import uvicorn
    except ImportError:
        print(
            "error: service dependencies are not installed. Run:\n"
            '  pip install -e ".[api]"',
            file=sys.stderr,
        )
        return 2

    # Imported lazily so a pure-CLI install never pays for FastAPI.
    from agentalyze.api.app import create_app
    from agentalyze.api.observability import configure_logging

    configure_logging(settings.log_level, json_format=settings.log_format == "json")
    if args.demo_mode:
        settings = Settings(**{**settings.model_dump(), "demo_mode_enabled": True})
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def _cmd_create_api_key(args: argparse.Namespace, settings: Settings) -> int:
    """`agentalyze create-api-key --name <client>`: print a key ONCE, store its hash."""
    try:
        from sqlalchemy import select

        from agentalyze.api.auth import generate_api_key, hash_api_key
        from agentalyze.api.db import (
            ApiKeyRecord,
            make_engine,
            make_session_factory,
            run_migrations,
        )
    except ImportError:
        print('error: install service dependencies first: pip install -e ".[api]"',
              file=sys.stderr)
        return 2

    async def _create() -> str:
        engine = make_engine(settings.database_url)
        try:
            await asyncio.to_thread(run_migrations, settings.database_url)
            factory = make_session_factory(engine)
            async with factory() as session:
                existing = await session.execute(
                    select(ApiKeyRecord).where(ApiKeyRecord.name == args.name)
                )
                if existing.scalar_one_or_none() is not None:
                    raise SystemExit(
                        f"error: an API key named {args.name!r} already exists"
                    )
                plaintext = generate_api_key()
                session.add(
                    ApiKeyRecord(
                        name=args.name, key_hash=hash_api_key(plaintext), is_active=True
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()
        return plaintext

    plaintext = asyncio.run(_create())
    print(f"API key for {args.name!r} (store it NOW — it is never shown again):")
    print(plaintext)
    return 0


def _cmd_revoke_api_key(args: argparse.Namespace, settings: Settings) -> int:
    try:
        from sqlalchemy import update

        from agentalyze.api.db import (
            ApiKeyRecord,
            make_engine,
            make_session_factory,
            run_migrations,
        )
    except ImportError:
        print('error: install service dependencies first: pip install -e ".[api]"',
              file=sys.stderr)
        return 2

    async def _revoke() -> int:
        engine = make_engine(settings.database_url)
        try:
            await asyncio.to_thread(run_migrations, settings.database_url)
            factory = make_session_factory(engine)
            async with factory() as session:
                result = await session.execute(
                    update(ApiKeyRecord)
                    .where(ApiKeyRecord.name == args.name)
                    .values(is_active=False)
                )
                await session.commit()
                rowcount: int = result.rowcount  # type: ignore[attr-defined]
                return rowcount
        finally:
            await engine.dispose()

    revoked = asyncio.run(_revoke())
    if revoked:
        print(f"Revoked {revoked} API key(s) named {args.name!r}.")
        return 0
    print(f"error: no active API key named {args.name!r}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    settings = Settings()

    if args.command == "tasks":
        _print_task_list(
            FailureTag(args.tag) if args.tag else None,
        )
        return 0

    # Service-mode commands handle their own settings; nothing below applies.
    if args.command == "serve":
        return _cmd_serve(args, settings)
    if args.command == "create-api-key":
        return _cmd_create_api_key(args, settings)
    if args.command == "revoke-api-key":
        return _cmd_revoke_api_key(args, settings)

    # Phase 5 commands handle their own provider loading / health checks.
    if args.command == "compare":
        from agentalyze.orchestration.cli import cmd_compare

        return cmd_compare(args, _apply_overrides(settings, args))

    if args.command == "inspect":
        from agentalyze.orchestration.cli import cmd_inspect

        return cmd_inspect(args, _apply_overrides(settings, args))

    if args.command == "regression-check":
        from agentalyze.regression.cli import cmd_regression_check

        return cmd_regression_check(args, _apply_overrides(settings, args))

    if args.command == "set-baseline":
        from agentalyze.regression.cli import cmd_set_baseline

        return cmd_set_baseline(args, _apply_overrides(settings, args))

    settings = _apply_overrides(settings, args)
    task = TASKS_BY_ID.get(args.task)
    if task is None:
        print(f"error: unknown task {args.task!r}; run `agentalyze tasks` to list ids.",
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

    if getattr(args, "agent_style", "tool_calling") == "code":
        try:
            from agentalyze.runner.code_agent.loop import run_task_code_agent
        except ImportError:
            print(
                "error: --agent-style code requires the code-agent extra:\n"
                '  pip install -e ".[code-agent]"',
                file=sys.stderr,
            )
            return 2
        trace = asyncio.run(run_task_code_agent(task, provider, settings))
    else:
        trace = asyncio.run(run_task(task, provider, settings))
    _print_summary(trace, task.title)
    return 0 if trace.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
