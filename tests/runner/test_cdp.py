"""run_task against a REMOTE browser over CDP — no mocks, real Chromium.

Validates the browser-infra split (Browserless-style deployments): the
runner must connect via ``connect_over_cdp`` instead of launching locally,
and task pages must come from ``fixture_base_url`` instead of the local
ephemeral fixture server. The "remote" browser here is a locally started
Chromium exposing a CDP endpoint — the same wire protocol the cloud
providers use.
"""

from __future__ import annotations

import asyncio
import selectors
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from agentalyze.config import Settings
from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner import run_task
from agentalyze.tasks.fixture_server import FixtureServer
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = pytest.mark.browser

REPO_ROOT = Path(__file__).parents[2]
REPO_FIXTURES = REPO_ROOT / "fixtures"


def _start_chromium_with_cdp() -> tuple[subprocess.Popen[str], str]:
    """Start a real headless Chromium with a CDP endpoint; return (proc, ws)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        executable = str(pw.chromium.executable_path)

    profile = tempfile.mkdtemp(prefix="agentalyze-cdp-profile-")
    proc = subprocess.Popen(
        [
            executable,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    selector = selectors.DefaultSelector()
    assert proc.stderr is not None
    selector.register(proc.stderr, selectors.EVENT_READ)
    while time.monotonic() < deadline:
        if not selector.select(timeout=0.5):
            continue
        line = proc.stderr.readline()
        if "DevTools listening on" in line:
            endpoint = line.split("listening on", 1)[1].strip()
            return proc, endpoint
    proc.kill()
    raise AssertionError("chromium did not expose a CDP endpoint in time")


class _ScriptedProvider:
    """Deterministic script: click the Documentation link, then declare done."""

    name = "scripted-cdp"

    def __init__(self) -> None:
        self._calls = 0

    async def chat_completion(
        self, messages: list[ChatMessage], tools: Any = None, **kwargs: Any
    ) -> CompletionResult:
        self._calls += 1
        if self._calls == 1:
            call = ToolCall(
                id="call_1", name="click", arguments={"element_id": "e4"}
            )
        else:
            call = ToolCall(id="call_2", name="done", arguments={"success": True})
        return CompletionResult(
            message=ChatMessage(role="assistant", content="", tool_calls=[call]),
            prompt_tokens=120,
            completion_tokens=10,
            total_tokens=130,
            latency_seconds=0.01,
            finish_reason="tool_calls",
        )

    async def health_check(self) -> bool:
        return True


def test_run_task_uses_remote_browser_via_cdp(tmp_path: Path) -> None:
    proc, endpoint = _start_chromium_with_cdp()
    server = FixtureServer(root=REPO_FIXTURES)
    server.start()
    try:
        settings = Settings(
            browser_cdp_endpoint=endpoint,
            fixtures_dir=REPO_FIXTURES,
            results_dir=tmp_path / "results",
        )
        trace = asyncio.run(
            run_task(
                TASKS_BY_ID["nav-simple-link-01"],
                _ScriptedProvider(),
                settings,
                fixture_base_url=server.base_url,
            )
        )
    finally:
        server.stop()
        proc.kill()

    assert trace.success, trace.error
    actions = [step.tool_call.name if step.tool_call else "" for step in trace.steps]
    assert "click" in actions
