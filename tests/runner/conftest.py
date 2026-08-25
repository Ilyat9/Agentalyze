"""Shared fixtures for the runner tests.

Same fixture set as ``tests/tasks/conftest.py`` (fixture server + real
Chromium); tests using the browser must be marked with
``pytest.mark.browser``. Also provides a tiny helper for building runner
``Settings`` pointed at this repo's fixtures and a temp results dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentalyze.config import Settings
from agentalyze.tasks.fixture_server import FixtureServer

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"


@pytest.fixture
async def fixture_server():
    async with FixtureServer() as server:
        yield server


@pytest.fixture
def playwright_module():
    pytest.importorskip(
        "playwright",
        reason="Playwright is required: pip install -e '.[browser]' && playwright install chromium",
    )
    from playwright.async_api import async_playwright

    return async_playwright


@pytest.fixture
async def browser(playwright_module):
    async with playwright_module() as pw:
        chromium = await pw.chromium.launch(headless=True)
        yield chromium
        await chromium.close()


@pytest.fixture
async def page(browser):
    context = await browser.new_context()
    yield await context.new_page()
    await context.close()


@pytest.fixture
def runner_settings(tmp_path: Path) -> Settings:
    """Settings pointing at the repo's fixtures and an isolated results dir."""
    return Settings(fixtures_dir=FIXTURES_DIR, results_dir=tmp_path / "results")
