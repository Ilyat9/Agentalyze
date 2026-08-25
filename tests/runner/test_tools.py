"""Browser tools against real Chromium and Phase 1 fixtures.

No model, no provider involved: tools are called directly with manual
arguments (same spirit as the Phase 1 fixture validator). Every test here
requires a real Chromium install -> marked ``browser``.
"""

from __future__ import annotations

import pytest

from agentalyze.providers.base import ToolCall
from agentalyze.runner.observation import build_observation
from agentalyze.runner.tools import (
    DONE_TOOL_NAME,
    TOOL_SPECS,
    ToolContext,
    do_click,
    do_extract_text,
    do_navigate,
    do_select_option,
    do_submit_form,
    do_type_text,
    do_wait_for,
    execute_tool,
)

pytestmark = pytest.mark.browser


async def _open(page, fixture_server, fixture_url_path: str) -> tuple[ToolContext, dict]:
    """Open a fixture, tag elements via one observation pass, return ctx+items."""
    await page.goto(fixture_server.base_url + fixture_url_path, wait_until="load")
    observation = await build_observation(page)
    return ToolContext(page=page, base_url=fixture_server.base_url), {
        item["name"]: item["id"] for item in observation.items
    }


def _call(name: str, **arguments) -> ToolCall:
    return ToolCall(id="test-call", name=name, arguments=arguments)


def _find_id(items_by_name: dict[str, str], fragment: str) -> str:
    matches = [eid for name, eid in items_by_name.items() if fragment.lower() in name.lower()]
    assert matches, f"no observed element contains {fragment!r}: {items_by_name}"
    return matches[0]


class TestNavigateSsrfGuard:
    async def test_relative_path_allowed(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await do_navigate(ctx, "/navigation/docs_01.html")
        assert result.success
        assert page.url.endswith("/navigation/docs_01.html")

    async def test_same_origin_absolute_allowed(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        url = f"{fixture_server.base_url}/navigation/docs_01.html"
        result = await do_navigate(ctx, url)
        assert result.success
        assert page.url == url

    async def test_foreign_origin_blocked(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await do_navigate(ctx, "http://evil.example.com/navigation/docs_01.html")
        assert not result.success
        assert "blocked" in result.output
        assert "simple_link_01" in page.url  # the page did not move

    async def test_other_port_blocked(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        smuggled = "http://127.0.0.1:9/navigation/docs_01.html"
        result = await do_navigate(ctx, smuggled)
        assert not result.success
        assert "port" in result.output

    async def test_non_http_scheme_blocked(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        for url in ("file:///etc/passwd", "ftp://127.0.0.1/x", "data:text/html,hi"):
            result = await do_navigate(ctx, url)
            assert not result.success, url

    async def test_execute_tool_returns_failure_not_exception(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await execute_tool(ctx, _call("navigate", url="http://example.com/"))
        assert isinstance(result.success, bool)
        assert not result.success


class TestClick:
    async def test_by_observation_id(self, page, fixture_server) -> None:
        ctx, items = await _open(page, fixture_server, "/navigation/dropdown_menu_02.html")
        first = await do_click(ctx, _find_id(items, "Products ▾"))
        assert first.success, first.output
        # The submenu only exists in the DOM after the parent toggle was clicked.
        observation = await build_observation(page)
        fresh_items = {i["name"]: i["id"] for i in observation.items}
        result = await do_click(ctx, _find_id(fresh_items, "Analytics Pro"))
        assert result.success
        opened = await page.locator('#dropdown-target[data-opened="true"]').count()
        assert opened == 1

    async def test_natural_language_fallback(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await do_click(ctx, "the Documentation link please")
        assert result.success
        assert "(matched by description)" in result.output
        assert page.url.endswith("/navigation/docs_01.html")

    async def test_unknown_element_fails_cleanly(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await do_click(ctx, "e999")
        assert not result.success
        assert "No element matches" in result.output


class TestTypeAndSelect:
    async def test_type_text(self, page, fixture_server) -> None:
        ctx, items = await _open(page, fixture_server, "/form_fill/basic_01.html")
        # The email textbox is named by its label text ("Email").
        email_id = _find_id(items, "Email")
        result = await do_type_text(ctx, email_id, "ivan@example.com")
        assert result.success
        assert await page.locator("#email").input_value() == "ivan@example.com"

    async def test_select_option_by_value_and_label(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/multi_step/wizard_02.html")
        # The combobox lives on wizard step 2: complete step 1 first.
        obs1 = await build_observation(page)
        name_id = next(i["id"] for i in obs1.items if "Your name" in i["name"])
        await do_type_text(ctx, name_id, "Tester")
        await do_click(ctx, next(i["id"] for i in obs1.items if i["name"] == "Next"))
        obs2 = await build_observation(page)
        combos = [i["id"] for i in obs2.items if i["role"] == "combobox"]
        assert combos
        select_el = page.locator("select").first
        option_value = await select_el.locator("option").nth(2).get_attribute("value")
        result = await do_select_option(ctx, combos[0], option_value)
        assert result.success
        assert await select_el.input_value() == option_value
        # Selecting by visible label must work as a fallback path.
        result = await do_select_option(ctx, combos[0], option_value.capitalize())
        assert result.success


class TestSubmitExtractWait:
    async def test_submit_form_by_button_id(self, page, fixture_server) -> None:
        ctx, items = await _open(page, fixture_server, "/form_fill/basic_01.html")
        await do_type_text(ctx, _find_id(items, "Name"), "Ivan Petrov")
        await do_type_text(ctx, _find_id(items, "Email"), "ivan@example.com")
        await do_type_text(ctx, _find_id(items, "Message"), "My order has not arrived")
        result = await do_submit_form(ctx, _find_id(items, "Send message"))
        assert result.success
        marker = page.locator("#success-marker")
        await marker.wait_for(state="visible", timeout=3000)
        assert await marker.is_visible()

    async def test_submit_form_without_id(self, page, fixture_server) -> None:
        ctx, items = await _open(page, fixture_server, "/form_fill/basic_01.html")
        await do_type_text(ctx, _find_id(items, "Name"), "X")
        result = await do_submit_form(ctx)
        assert result.success
        await page.locator("#success-marker").wait_for(state="visible", timeout=3000)

    async def test_extract_text_from_table_cell(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/extraction/price_01.html")
        observation = await build_observation(page)
        price_id = None
        for item in observation.items:
            if "$1,249.90" in item["name"]:
                price_id = item["id"]
                break
        assert price_id is not None
        result = await do_extract_text(ctx, price_id)
        assert result.success
        assert "$1,249.90" in result.output

    async def test_wait_for_text_success_and_timeout(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/form_fill/basic_01.html")
        ok = await do_wait_for(ctx, "'Contact Support'", 3)
        assert ok.success

        miss = await do_wait_for(ctx, "'No such text anywhere xyz'", 1)
        assert not miss.success
        assert "Timed out" in miss.output


class TestDispatchAndDone:
    async def test_unknown_tool_returns_failure(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await execute_tool(ctx, _call("delete_database"))
        assert not result.success
        assert "Unknown tool" in result.output
        # The hint lists every available tool name.
        for spec in TOOL_SPECS:
            assert spec.name in result.output

    async def test_bad_arguments_return_failure_not_crash(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        result = await execute_tool(ctx, _call("type_text", element_id="e1"))  # missing 'text'
        assert not result.success
        assert "failed" in result.output

    async def test_done_tool_acknowledges(self, page, fixture_server) -> None:
        ctx, _ = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        ok = await execute_tool(
            ctx,
            _call(DONE_TOOL_NAME, success=True, extracted_value="42", confidence=0.9),
        )
        assert ok.success
        gave_up = await execute_tool(ctx, _call(DONE_TOOL_NAME, success=False))
        assert not gave_up.success

    async def test_tool_specs_have_unique_names_and_schemas(self) -> None:
        names = [spec.name for spec in TOOL_SPECS]
        assert len(names) == len(set(names))
        required_tools = {
            "navigate", "click", "type_text", "select_option",
            "submit_form", "extract_text", "wait_for", DONE_TOOL_NAME,
        }
        assert required_tools <= set(names)


class TestObservationIntegration:
    async def test_stale_id_after_navigation_resolves_via_fallback_or_fails_cleanly(
        self, page, fixture_server
    ) -> None:
        """Ids are per-step: after navigation the old id must not silently hit the wrong node."""
        ctx, items = await _open(page, fixture_server, "/navigation/simple_link_01.html")
        docs_id = _find_id(items, "Documentation")
        await do_navigate(ctx, "/form_fill/basic_01.html")
        result = await do_click(ctx, docs_id)  # stale id -> fallback or clean failure
        if result.success:
            assert "(matched by description)" in result.output
        else:
            assert "No element matches" in result.output



