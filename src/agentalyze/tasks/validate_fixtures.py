"""Programmatic (non-agent) validation of all registered fixtures.

Drives every fixture with Playwright exactly the way ``reference.py``
prescribes and checks that:

1. the page loads without JavaScript console errors,
2. all reference steps are executable,
3. the DOM reaches the fixture's success marker,
4. the task's own verifier agrees that this final state is a success.

This is a fixture self-check, NOT an agent run: there is no LLM and no
ReAct loop here. Run it as a module::

    python -m agentalyze.tasks.validate_fixtures
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from playwright.async_api import Browser, Page

from agentalyze.tasks.fixture_server import FixtureServer
from agentalyze.tasks.models import Task
from agentalyze.tasks.reference import REFERENCE, ReferenceStep
from agentalyze.tasks.registry import TASKS
from agentalyze.tasks.verifiers import VERIFIERS


@dataclass
class ValidationReport:
    """Outcome of validating a single fixture end-to-end."""

    task_id: str
    ok: bool
    reason: str
    console_errors: list[str] = field(default_factory=list)


async def _apply_step(page: Page, step: ReferenceStep) -> None:
    locator = page.locator(step.selector).first
    if step.action == "fill":
        await locator.fill(step.value or "", timeout=3_000)
    elif step.action == "click":
        await locator.click(timeout=3_000)
    elif step.action == "select":
        await locator.select_option(step.value or "", timeout=3_000)
    elif step.action == "check":
        await locator.check(timeout=3_000)


async def validate_task(browser: Browser, base_url: str, task: Task) -> ValidationReport:
    """Validate one fixture: drive it programmatically, then verify + check console."""
    reference = REFERENCE[task.id]
    console_errors: list[str] = []

    context = await browser.new_context()
    page = await context.new_page()
    try:
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        await page.goto(base_url + task.fixture_url_path, wait_until="load")

        for step in reference.steps:
            await _apply_step(page, step)

        await page.locator(reference.success_selector).first.wait_for(
            state="visible", timeout=5_000
        )

        verifier = VERIFIERS[task.verifier_id]
        result = await verifier.verify(page)
        if not result.success:
            return ValidationReport(
                task.id, False, f"Verifier rejected the driven state: {result.reason}"
            )
        if console_errors:
            return ValidationReport(
                task.id,
                False,
                "Page produced JS console errors",
                console_errors=console_errors,
            )
        return ValidationReport(task.id, True, result.reason)
    except Exception as exc:  # noqa: BLE001 - report any driver failure per task
        return ValidationReport(
            task.id,
            False,
            f"Validation driver failed: {type(exc).__name__}: {exc}",
            console_errors=console_errors,
        )
    finally:
        await context.close()


async def validate_all(browser: Browser, base_url: str) -> list[ValidationReport]:
    """Validate every task in the registry against one running fixture server."""
    return [await validate_task(browser, base_url, task) for task in TASKS]


async def _main() -> int:
    from playwright.async_api import async_playwright

    async with FixtureServer() as server, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            reports = await validate_all(browser, server.base_url)
        finally:
            await browser.close()

    failed = 0
    for report in reports:
        status = "PASS" if report.ok else "FAIL"
        print(f"[{status}] {report.task_id}: {report.reason}")
        for err in report.console_errors:
            print(f"       console error: {err}")
        failed += 0 if report.ok else 1
    print(f"\n{len(reports) - failed}/{len(reports)} fixtures valid")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
