"""Shared fixtures for the task-suite tests.

The ``fixture_server`` fixture is cheap and used by both plain unit tests and
browser tests. The ``browser``/``page`` fixtures require a real Chromium
install; tests using them must be marked with ``pytest.mark.browser`` (the
marker is registered in ``pyproject.toml`` and excluded from the default run).
"""

from __future__ import annotations

import pytest

from agentalyze.tasks.fixture_server import FixtureServer


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
