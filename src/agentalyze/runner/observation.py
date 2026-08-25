"""Compact, labeled page observation — what the agent "sees" each step.

Design notes
------------
The phase spec suggests building the observation from Playwright's
``accessibility.snapshot()``. We keep the *semantics* of an accessibility
tree (ARIA-like role + accessible name per element) but generate it with a
small in-page JS scan instead of consuming the raw snapshot, for one
practical reason: the raw a11y snapshot is unidirectional — there is no way
to map a node from it back to a Playwright ``Locator``, which the
click/type/extract tools require. Our scan assigns deterministic ids
(``e1``, ``e2``, ... in document order for the current step) *and* tags the
live DOM elements with ``data-agentalyze-id``, giving tools exact resolution.

Ids are intentionally NOT stable across steps: the DOM may change between
actions, so the agent must re-read the fresh observation each step rather
than memorize identifiers. Deterministic ordering within one step keeps
observations reproducible for tests.

Size control: names and static text are truncated per element, the element
count is capped, and the final text is hard-capped. The DOM hash is computed
BEFORE tagging so the harness's own attributes never influence it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

#: Hard cap on the rendered observation; if fixtures ever exceed this, the
#: filtering below must be tightened rather than raising the cap silently.
MAX_OBSERVATION_CHARS = 12_000
MAX_ITEMS = 150

_TAGGING_JS = """
() => {
  // Drop ids from previous observation passes: otherwise a stale id could
  // resolve to both an old (now-hidden) element and a fresh visible one.
  document
    .querySelectorAll('[data-agentalyze-id]')
    .forEach((el) => el.removeAttribute('data-agentalyze-id'));

  const INTERACTIVE = new Set([
    'link', 'button', 'textbox', 'combobox', 'checkbox', 'radio',
    'searchbox', 'switch', 'tab', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'option', 'slider',
  ]);
  const collapse = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  const isVisible = (el) => {
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({ checkVisibilityCSS: true });
    }
    return !!(el.offsetParent || el.getClientRects().length);
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || (tag === 'input' && ['button', 'submit', 'reset'].includes(type))) {
      return 'button';
    }
    if (tag === 'input') {
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'label') return 'label';
    return 'text';
  };

  const nameOf = (el) => {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return collapse(ariaLabel);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const lbl = document.getElementById(labelledBy);
      if (lbl) return collapse(lbl.innerText);
    }
    const tag = el.tagName.toLowerCase();
    if ((tag === 'input' || tag === 'textarea') && el.id) {
      const lbl = document.querySelector('label[for="' + el.id + '"]');
      if (lbl) return collapse(lbl.innerText);
    }
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return collapse(placeholder);
    const value = el.value !== undefined ? String(el.value) : '';
    if (value && roleOf(el) !== 'text') return collapse(value);
    // Field wrapped by its <label> ("Name <input>") gets the label's text.
    const wrapLabel = el.closest ? el.closest('label') : null;
    if (wrapLabel) return collapse(wrapLabel.innerText);
    return collapse(el.innerText);
  };

  const SELECTOR = [
    'a', 'button', 'input', 'textarea', 'select', 'label',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'li', 'th', 'td', 'caption', 'dt', 'dd', 'blockquote', 'summary',
    '[role]',
  ].join(', ');

  const seen = [];
  const items = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (!isVisible(el)) continue;
    // Skip nodes whose text is fully represented by an already-listed
    // ancestor (e.g. <label> wrapping both its text and the <input>).
    let dominated = false;
    for (const anc of seen) {
      if (anc.contains(el) && collapse(anc.innerText) === collapse(el.innerText)) {
        dominated = true;
        break;
      }
    }
    if (dominated) continue;
    seen.push(el);
    const id = 'e' + (items.length + 1);
    el.setAttribute('data-agentalyze-id', id);
    const role = roleOf(el);
    items.push({
      id,
      role,
      interactive: INTERACTIVE.has(role),
      name: nameOf(el).slice(0, 160),
      value: role === 'textbox' || role === 'combobox'
        ? collapse(el.value || '').slice(0, 160)
        : '',
      disabled: !!el.disabled,
      href: role === 'link' ? el.getAttribute('href') : null,
    });
    if (items.length >= 150) break;
  }
  return { title: document.title, path: location.pathname, items };
}
"""


@dataclass
class PageObservation:
    """Structured result of one observation pass."""

    text: str
    dom_hash: str
    #: Raw scanned items (id/role/name/...), useful for tests and debugging.
    items: list[dict[str, Any]] = field(default_factory=list)


def dom_snapshot_hash(html: str) -> str:
    """Cheap fingerprint of a page state: sha256 of whitespace-normalized HTML."""
    normalized = re.sub(r"\s+", " ", html.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def render_observation(data: dict[str, Any]) -> str:
    """Render the JS scan result into the compact text the model reads."""
    lines = [f"PAGE: {data['title']} ({data['path']})", "ELEMENTS:"]
    for item in data["items"]:
        ident, role, name = item["id"], item["role"], item["name"]
        if item.get("interactive"):
            line = f'[{ident}] {role} "{name}"'
            if item.get("value"):
                line += f' value="{item["value"]}"'
            if item.get("disabled"):
                line += " (disabled)"
            if item.get("href"):
                line += f" -> {item['href']}"
        else:
            line = f'[{ident}] {role}: "{name}"'
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > MAX_OBSERVATION_CHARS:
        text = text[:MAX_OBSERVATION_CHARS] + "\n...[observation truncated]"
    return text


async def build_observation(page: Page) -> PageObservation:
    """Scan the current page and return the compact observation for the model.

    Side effect: scanned elements get a ``data-agentalyze-id`` attribute so
    tools can resolve ``element_id`` values exactly.
    """
    # Hash BEFORE tagging: the injected attributes must not affect the hash.
    html = await page.content()
    data = await page.evaluate(_TAGGING_JS)
    return PageObservation(
        text=render_observation(data),
        dom_hash=dom_snapshot_hash(html),
        items=list(data["items"]),
    )
