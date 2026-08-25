"""Observation quality tests against real fixtures (marker: browser)."""

from __future__ import annotations

import pytest

from agentalyze.runner.observation import (
    MAX_ITEMS,
    MAX_OBSERVATION_CHARS,
    build_observation,
    dom_snapshot_hash,
)
from agentalyze.tasks.registry import TASKS

pytestmark = pytest.mark.browser


class TestObservationContent:
    async def test_links_and_headings_present_with_ids(self, page, fixture_server) -> None:
        await page.goto(fixture_server.base_url + "/navigation/simple_link_01.html")
        obs = await build_observation(page)

        assert "PAGE: Acme Portal" in obs.text
        assert '[e1] heading: "Acme Portal"' in obs.text  # deterministic document-order ids
        assert '[e3] link "Home"' in obs.text
        assert '[e4] link "Documentation" -> /navigation/docs_01.html' in obs.text
        assert 'text: "The office will be closed on Friday."' in obs.text

    async def test_form_fields_show_labels_and_values(self, page, fixture_server) -> None:
        await page.goto(fixture_server.base_url + "/form_fill/basic_01.html")
        obs = await build_observation(page)
        assert '[e3] textbox "Name"' in obs.text  # name from the wrapping <label>
        assert '"Send message"' in obs.text

    async def test_hidden_elements_excluded_until_visible(self, page, fixture_server) -> None:
        await page.goto(fixture_server.base_url + "/navigation/dropdown_menu_02.html")
        before = await build_observation(page)
        assert not any("Analytics Pro" in i["name"] for i in before.items)

        await page.locator("[data-menu-toggle='products']").click()
        after = await build_observation(page)
        assert any("Analytics Pro" in i["name"] for i in after.items)

    async def test_deterministic_across_runs(self, browser, playwright_module, fixture_server) -> None:
        hashes = []
        for _ in range(2):
            context = await browser.new_context()
            p = await context.new_page()
            await p.goto(fixture_server.base_url + "/extraction/price_01.html")
            obs = await build_observation(p)
            hashes.append(obs.dom_hash)
            await context.close()
        assert hashes[0] == hashes[1]


class TestObservationSizeBounds:
    async def test_all_registered_fixtures_stay_bounded(self, page, fixture_server) -> None:
        """The observation must never explode on the suite's most complex fixtures."""
        for task in TASKS:
            await page.goto(fixture_server.base_url + task.fixture_url_path, wait_until="load")
            obs = await build_observation(page)
            assert len(obs.text) <= MAX_OBSERVATION_CHARS, f"observation too big for {task.id}"
            assert len(obs.items) <= MAX_ITEMS, f"too many items for {task.id}"
            # Every item id is well-formed and unique within a step.
            ids = [i["id"] for i in obs.items]
            assert len(ids) == len(set(ids))
            assert all(id.startswith("e") and id[1:].isdigit() for id in ids)

    async def test_dom_hash_changes_when_page_changes(self, page, fixture_server) -> None:
        await page.goto(fixture_server.base_url + "/form_fill/basic_01.html")
        first = await build_observation(page)
        await page.locator("#name").fill("changed!")
        second = await build_observation(page)
        assert first.dom_hash != second.dom_hash


def test_dom_snapshot_hash_is_stable_and_normalized() -> None:
    assert dom_snapshot_hash("<a>  b   c</a>") == dom_snapshot_hash("<a> b c</a>")
    assert dom_snapshot_hash("x") != dom_snapshot_hash("y")
