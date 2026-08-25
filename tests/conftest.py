"""Root test configuration.

Browser tests are excluded from the default run via ``addopts`` in
``pyproject.toml``; the hook below makes that exclusion visible when it
silently swallows an explicitly requested test file (e.g. running
``pytest tests/tasks/test_verifiers.py`` without ``-m browser`` would
otherwise report "0 selected" with no explanation).
"""

from __future__ import annotations


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Hint when every requested test was deselected by the 'browser' marker."""
    if terminalreporter.stats.get("passed") or terminalreporter.stats.get("failed"):
        return  # something ran; the default-run deselection is intentional

    deselected = [rep for rep in terminalreporter.stats.get("deselected", [])]
    browser_deselected = [rep for rep in deselected if rep.keywords.get("browser")]
    if not browser_deselected:
        return

    terminalreporter.section("hint", yellow=True)
    terminalreporter.line(
        f"{len(browser_deselected)} collected test(s) were deselected because they "
        "require a real Chromium (marker 'browser' is excluded from the default run).\n"
        "Run them explicitly:  pytest -m browser\n"
        "(requires: pip install -e '.[browser]' && playwright install chromium)"
    )
