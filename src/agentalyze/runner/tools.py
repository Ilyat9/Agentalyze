"""Browser tool set exposed to the agent via provider tool calling.

Each tool is a pair: a :class:`~agentalyze.providers.base.ToolSpec` (what the
model sees) and an async function executing the action through a real
Playwright ``Page``. The set is deliberately minimal but complete for the
Phase 1 task suite — including ``select_option``, which the suite's two
``<select>`` fixtures require.

Security / eval-honesty guard
-----------------------------
The agent is confined to the fixture-server origin issued for its run:
``navigate`` accepts only relative paths or absolute URLs on exactly that
origin (same scheme, host ``127.0.0.1``, same port). This is both SSRF
hygiene and fairness — wandering off the test range is neither a safety
accident nor a legitimate solution path. Violations return a failed
``ToolResult`` as a normal observation; they never crash the run.

Element resolution strategy (variant (a), with fallback)
--------------------------------------------------------
Observations assign deterministic ids (``e1``, ``e2``, ...) and tag the live
DOM with ``data-agentalyze-id``; tools resolve those ids exactly. If a model
passes something else (a natural-language description), tools fall back to
fuzzy lookup by role/text/label — mirroring how real browser agents degrade
gracefully when their snapshot reference goes stale.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agentalyze.providers.base import ToolCall, ToolSpec
from agentalyze.runner.trace import ToolResult

DONE_TOOL_NAME = "done"
_ELEMENT_ID_RE = re.compile(r"^e\d+$")
_ACTION_TIMEOUT_MS = 5_000
_NAVIGATE_TIMEOUT_MS = 15_000

#: Words ignored when fuzzy-matching natural-language element descriptions.
_FUZZY_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "with",
    "please", "element", "field", "button", "link", "tab", "box",
    "click", "press", "open", "find",
}


# ---------------------------------------------------------------------------
# ToolSpecs (the model-facing half).
# ---------------------------------------------------------------------------

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="navigate",
        description=(
            "Navigate the current tab to a URL. Only URLs on this task's own "
            "site (relative paths like '/docs/index.html' or absolute URLs "
            "with the same host and port you started on) are allowed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Relative path or absolute URL."},
            },
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="click",
        description=(
            "Click a page element by its id from the latest ELEMENTS list "
            "(e.g. 'e3'). A short natural-language description of the element "
            "(e.g. 'Documentation link') is accepted as a fallback but ids "
            "are preferred and more reliable."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {
                    "type": "string",
                    "description": "Element id from the observation, e.g. 'e3'.",
                },
            },
            "required": ["element_id"],
        },
    ),
    ToolSpec(
        name="type_text",
        description="Replace the content of a text field (textbox) with the given text.",
        parameters={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "Textbox id from the observation."},
                "text": {"type": "string", "description": "Text to enter."},
            },
            "required": ["element_id", "text"],
        },
    ),
    ToolSpec(
        name="select_option",
        description=(
            "Choose an option in a dropdown (combobox). Tries to match the "
            "option by value first, then by visible label."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "Combobox id from the observation."},
                "value": {"type": "string", "description": "Option value or visible label."},
            },
            "required": ["element_id", "value"],
        },
    ),
]

TOOL_SPECS += [
    ToolSpec(
        name="submit_form",
        description=(
            "Submit a form: click the given submit button id, or, without an "
            "id, click the page's primary submit button."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {
                    "type": "string",
                    "description": "Optional submit button id from the observation.",
                },
            },
        },
    ),
    ToolSpec(
        name="extract_text",
        description=(
            "Read the visible text content of any element from the "
            "observation (headings, paragraphs, table cells, inputs)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "Element id from the observation."},
            },
            "required": ["element_id"],
        },
    ),
]

TOOL_SPECS += [
    ToolSpec(
        name="wait_for",
        description=(
            "Wait until the given text appears on the page (or a CSS selector "
            "if one is provided). Use after actions whose effect is async."
        ),
        parameters={
            "type": "object",
            "properties": {
                "condition_description": {
                    "type": "string",
                    "description": (
                        'Text expected to appear (quote it, e.g. "\'Report generated\'") '
                        "or a CSS selector like '#success-marker'."
                    ),
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["condition_description", "timeout_seconds"],
        },
    ),
    ToolSpec(
        name=DONE_TOOL_NAME,
        description=(
            "Declare the task finished. Call this exactly once when you are "
            "confident the goal is achieved — or use success=false to give up. "
            "For extraction tasks pass extracted_value (the fact you found) "
            "and your confidence in [0, 1]. Nothing runs after this call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "extracted_value": {
                    "type": ["string", "null"],
                    "description": "The value you extracted, for extraction tasks.",
                },
                "confidence": {
                    "type": ["number", "null"],
                    "description": "Your confidence in [0, 1], for extraction tasks.",
                },
            },
            "required": ["success"],
        },
    ),
]

_TOOL_NAMES = ", ".join(spec.name for spec in TOOL_SPECS)


# ---------------------------------------------------------------------------
# Execution context.
# ---------------------------------------------------------------------------


class ToolContext:
    """Per-run state shared by all tools: the live page plus its allowed origin."""

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url.rstrip("/")

    # -- URL guard ----------------------------------------------------------

    def check_url(self, url: str) -> tuple[bool, str]:
        """SSRF/confinement guard. Returns ``(allowed, resolved_url_or_reason)``."""
        parts = urlsplit(url.strip())
        if not parts.scheme and parts.path.startswith("/"):
            suffix = f"?{parts.query}" if parts.query else ""
            fragment = f"#{parts.fragment}" if parts.fragment else ""
            return True, self.base_url + parts.path + suffix + fragment
        if not parts.scheme:
            reason = (
                f"URL {url!r} has no scheme; use an absolute http URL or a "
                "path starting with '/'"
            )
            return False, reason

        allowed = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https"):
            return False, f"scheme {parts.scheme!r} is blocked; only {allowed.scheme!r} is allowed"
        if parts.username or parts.password:
            return False, "credentials in URLs are blocked"

        if (parts.hostname or "") != allowed.hostname:
            reason = f"host {parts.hostname!r} is outside this task's site ({allowed.hostname!r})"
            return False, reason
        target_port = parts.port or (443 if parts.scheme == "https" else 80)
        if target_port != allowed.port:
            return False, f"port {target_port} is outside this task's site (port {allowed.port})"
        return True, urljoin(self.base_url + "/", url)

    # -- Element resolution ---------------------------------------------------

    async def resolve_element(self, element_id: str) -> Locator | None:
        """Exact resolution via the observation-assigned DOM attribute."""
        if not _ELEMENT_ID_RE.match(element_id):
            return None
        locator = self.page.locator(f"[data-agentalyze-id='{element_id}']")
        try:
            if await locator.count() > 0:
                return locator.first
        except PlaywrightError:
            return None
        return None

    async def fuzzy_locate(self, description: str) -> Locator | None:
        """Fallback: find an element from a natural-language description."""
        needle = description.strip().strip("\"'`")
        if not needle or _ELEMENT_ID_RE.match(needle):
            return None
        # Reduce "the Documentation link please" to its significant words and
        # try them (longest first) as case-insensitive substrings against
        # accessible names / text.
        words = [
            w
            for w in re.findall(r"[\w'-]+", needle)
            if w.lower() not in _FUZZY_STOPWORDS
        ]
        if not words:
            return None
        patterns = [
            re.compile(re.escape(word), re.IGNORECASE)
            for word in sorted(words, key=len, reverse=True)
        ]

        candidates: list[Locator] = []
        for role in ("link", "button", "tab", "menuitem", "checkbox", "combobox", "textbox"):
            candidates.append(self.page.get_by_role(role, name=needle, exact=True))
            candidates.extend(self.page.get_by_role(role, name=pattern) for pattern in patterns)
        candidates.extend(self.page.get_by_label(pattern) for pattern in patterns)
        candidates.extend(self.page.get_by_text(pattern) for pattern in patterns)
        for locator in candidates:
            try:
                if await locator.count() > 0:
                    return locator.first
            except PlaywrightError:
                continue
        return None

    async def resolve_any(self, element_id: str) -> Locator | None:
        """Resolve an id, degrading to fuzzy matching when the id is stale/unknown."""
        locator = await self.resolve_element(element_id)
        if locator is not None:
            return locator
        return await self.fuzzy_locate(element_id)


# ---------------------------------------------------------------------------
# Concrete tool implementations.
# ---------------------------------------------------------------------------


async def do_navigate(ctx: ToolContext, url: str) -> ToolResult:
    allowed, resolved = ctx.check_url(url)
    if not allowed:
        return ToolResult(success=False, output=f"Navigation blocked: {resolved}")
    await ctx.page.goto(resolved, wait_until="load", timeout=_NAVIGATE_TIMEOUT_MS)
    # Defense in depth: goto() follows redirects, so re-verify where the
    # browser actually landed; a same-origin URL must never move us off-site.
    landed_ok, landed_reason = ctx.check_url(ctx.page.url)
    if not landed_ok:
        return ToolResult(
            success=False,
            output=f"Navigation left the allowed site after a redirect: {landed_reason}",
        )
    title = await ctx.page.title()
    return ToolResult(success=True, output=f"Navigated to {ctx.page.url} (title: {title!r}).")


async def do_click(ctx: ToolContext, element_id: str) -> ToolResult:
    locator = await ctx.resolve_element(element_id)
    used_fallback = False
    if locator is None:
        locator = await ctx.fuzzy_locate(element_id)
        used_fallback = True
    if locator is None:
        return ToolResult(
            success=False,
            output=(
                f"No element matches {element_id!r}. Re-read the ELEMENTS "
                "list and pick an existing id."
            ),
        )
    label = ((await locator.text_content()) or "").strip()[:80]
    await locator.click(timeout=_ACTION_TIMEOUT_MS)
    note = " (matched by description)" if used_fallback else ""
    return ToolResult(
        success=True,
        output=f"Clicked {element_id}{note}" + (f' "{label}".' if label else "."),
    )


async def do_type_text(ctx: ToolContext, element_id: str, text: str) -> ToolResult:
    locator = await ctx.resolve_any(element_id)
    if locator is None:
        return ToolResult(success=False, output=f"No element matches {element_id!r}.")
    await locator.fill(text, timeout=_ACTION_TIMEOUT_MS)
    return ToolResult(success=True, output=f"Typed into {element_id}: {text!r}.")


async def do_select_option(ctx: ToolContext, element_id: str, value: str) -> ToolResult:
    locator = await ctx.resolve_any(element_id)
    if locator is None:
        return ToolResult(success=False, output=f"No element matches {element_id!r}.")
    try:
        await locator.select_option(value, timeout=_ACTION_TIMEOUT_MS)
    except PlaywrightError:
        # Retry matching by visible label instead of the raw value.
        await locator.select_option(label=value, timeout=_ACTION_TIMEOUT_MS)
    return ToolResult(success=True, output=f"Selected {value!r} in {element_id}.")


async def do_submit_form(ctx: ToolContext, element_id: str | None = None) -> ToolResult:
    if element_id:
        locator = await ctx.resolve_any(element_id)
        if locator is None:
            return ToolResult(success=False, output=f"No element matches {element_id!r}.")
        await locator.click(timeout=_ACTION_TIMEOUT_MS)
        return ToolResult(success=True, output=f"Submitted form via {element_id}.")
    button = ctx.page.locator("form button[type='submit'], form input[type='submit']").first
    if await button.count() > 0:
        await button.click(timeout=_ACTION_TIMEOUT_MS)
        return ToolResult(success=True, output="Submitted the form via its primary submit button.")
    form = ctx.page.locator("form").first
    if await form.count() == 0:
        return ToolResult(success=False, output="No form found on the page.")
    await form.evaluate("el => el.requestSubmit()")
    return ToolResult(success=True, output="Submitted the form programmatically.")

async def do_extract_text(ctx: ToolContext, element_id: str) -> ToolResult:
    locator = await ctx.resolve_any(element_id)
    if locator is None:
        return ToolResult(success=False, output=f"No element matches {element_id!r}.")
    text = ((await locator.inner_text()) or "").strip()
    if not text:
        text = ((await locator.text_content()) or "").strip()
        try:
            text = text or (await locator.input_value())
        except PlaywrightError:
            pass
    if not text:
        return ToolResult(success=False, output=f"{element_id} has no visible text.")
    return ToolResult(success=True, output=f"Text of {element_id}: {text[:1000]}")


async def do_wait_for(
    ctx: ToolContext, condition_description: str, timeout_seconds: int = 5
) -> ToolResult:
    target = condition_description.strip()
    quoted = re.search(r"[\"'](.+?)[\"']", target)
    if quoted:
        target = quoted.group(1)
    timeout_ms = max(1_000, min(int(timeout_seconds), 30) * 1000)

    try:
        await ctx.page.get_by_text(target).first.wait_for(state="visible", timeout=timeout_ms)
        return ToolResult(success=True, output=f"Condition met: text {target!r} is now visible.")
    except PlaywrightTimeoutError:
        pass

    looks_like_selector = bool(re.search(r"[#\[]|^\.", target)) and " " not in target
    if looks_like_selector:
        try:
            await ctx.page.locator(target).first.wait_for(state="visible", timeout=timeout_ms)
            return ToolResult(success=True, output=f"Condition met: selector {target!r} is visible.")
        except PlaywrightTimeoutError:
            pass
        except PlaywrightError:
            return ToolResult(success=False, output=f"Invalid selector: {target!r}")

    return ToolResult(
        success=False,
        output=f"Timed out after {timeout_seconds}s waiting for {target!r}.",
    )


async def do_done(
    ctx: ToolContext,
    success: bool,
    extracted_value: str | None = None,
    confidence: float | None = None,
) -> ToolResult:
    verdict = "completed" if success else "given up"
    extras = []
    if extracted_value is not None:
        extras.append(f"extracted_value={extracted_value!r}")
    if confidence is not None:
        extras.append(f"confidence={confidence}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    # Stamp the agent's OWN verdict into the page DOM so DOM-only verifiers
    # can tell "declared success" from "gave up" without inspecting agent
    # steps (the verifier protocol intentionally receives only the page).
    # Best-effort: the ack result below stays the authoritative record; an
    # about:blank or closing page must never crash the final step.
    # NOTE: evaluate() has no built-in timeout and HANGS indefinitely if the
    # page is mid-navigation (execution context not yet swapped), so this is
    # wrapped in a hard asyncio deadline — a lost stamp is acceptable, a hung
    # final step is not (it once wedged the whole CI browser suite).
    try:
        await asyncio.wait_for(
            ctx.page.evaluate(
                "v => document.documentElement.setAttribute('data-agent-verdict', v)",
                "success" if success else "given-up",
            ),
            timeout=5.0,
        )
    except (TimeoutError, PlaywrightError):
        pass
    return ToolResult(
        success=bool(success),
        output=f"Agent declared the task {verdict}{suffix}.",
    )

_DISPATCH: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    "navigate": do_navigate,
    "click": do_click,
    "type_text": do_type_text,
    "select_option": do_select_option,
    "submit_form": do_submit_form,
    "extract_text": do_extract_text,
    "wait_for": do_wait_for,
    DONE_TOOL_NAME: do_done,
}


async def execute_tool(ctx: ToolContext, call: ToolCall) -> ToolResult:
    """Run one tool call, converting *expected* failures into observations.

    Playwright errors (stale elements, timeouts, invalid selectors) and bad
    arguments are normal agent-visible outcomes: they come back as
    ``ToolResult(success=False, ...)``. Anything else is a genuine runner bug
    and propagates so the loop classifies it as ``FAILURE_TOOL_ERROR``.
    """
    fn = _DISPATCH.get(call.name)
    if fn is None:
        return ToolResult(
            success=False,
            output=f"Unknown tool {call.name!r}. Available tools: {_TOOL_NAMES}.",
        )
    arguments = call.arguments if isinstance(call.arguments, dict) else {}
    try:
        return await fn(ctx, **arguments)
    except (PlaywrightError, TypeError, ValueError) as exc:
        return ToolResult(
            success=False,
            output=f"Tool '{call.name}' failed: {type(exc).__name__}: {exc}",
        )






