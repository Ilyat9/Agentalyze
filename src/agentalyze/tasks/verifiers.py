"""Programmatic success verifiers.

A verifier answers exactly one question: *does the final DOM state of the page
match what the task expects?* It receives an already-open Playwright ``Page``
in its final state and inspects the DOM — it never inspects or replays agent
steps (that belongs to the failure-taxonomy layer in Phase 4).

Verifiers are resolved through the ``VERIFIERS`` registry (a plain dict keyed
by ``Task.verifier_id``) instead of an ``if/elif`` chain, so new verifiers can
be added without touching existing code.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Protocol, runtime_checkable

from playwright.async_api import Page

from agentalyze.tasks.models import VerificationResult

# Selectors that EXTRACTION fixtures use to record the agent's submitted answer.
ANSWER_VALUE_SELECTOR = "#recorded-answer"
ANSWER_CONFIDENCE_SELECTOR = "#recorded-confidence"


@runtime_checkable
class Verifier(Protocol):
    """Anything that can decide success from a final Playwright page state."""

    async def verify(self, page: Page) -> VerificationResult: ...


# ---------------------------------------------------------------------------
# Shared normalisation helpers (kept deliberately simple: no fuzzy matching).
# ---------------------------------------------------------------------------


def normalize_number(raw: str) -> float | None:
    """Parse '1,249.90', '$1249.90', '3' etc. into a float, or None."""
    cleaned = re.sub(r"[^\d.\-]", "", raw.strip())
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_date(raw: str) -> date | None:
    """Parse common date spellings ('2027-03-14', 'March 14, 2027', ...)."""
    text = re.sub(r"\s+", " ", raw.strip())
    formats = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d.%m.%Y")
    for fmt in formats:
        try:
            return date.strptime(text, fmt)
        except ValueError:
            continue
    return None


def normalize_text(raw: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", raw.strip().lower())


async def _read_answer_fields(page: Page) -> tuple[str, str]:
    value = await page.locator(ANSWER_VALUE_SELECTOR).text_content() or ""
    confidence = await page.locator(ANSWER_CONFIDENCE_SELECTOR).text_content() or ""
    return value, confidence


def _confidence_result(confidence_raw: str) -> VerificationResult | None:
    """Validate the agent-supplied confidence label; None when it is fine."""
    number = normalize_number(confidence_raw)
    if number is None:
        return VerificationResult(
            success=False,
            reason=(
                "The answer was submitted but no parsable confidence value "
                f"was recorded (got {confidence_raw.strip()!r}); the task "
                "explicitly requires a confidence alongside the value."
            ),
        )
    if not 0.0 <= number <= 1.0:
        return VerificationResult(
            success=False,
            reason=f"Recorded confidence {number} is outside the [0, 1] range.",
        )
    return None


# ---------------------------------------------------------------------------
# Concrete verifiers.
# ---------------------------------------------------------------------------


class ElementPresentVerifier:
    """Success = the given selector exists (and may be required visible)."""

    def __init__(self, selector: str, description: str, *, visible: bool = True) -> None:
        self.selector = selector
        self.description = description
        self.visible = visible

    async def verify(self, page: Page) -> VerificationResult:
        locator = page.locator(self.selector)
        try:
            state = "visible" if self.visible else "attached"
            await locator.first.wait_for(state=state, timeout=2_000)
        except Exception:  # noqa: BLE001 - any wait failure simply means "not present"
            return VerificationResult(
                success=False,
                reason=(
                    f"{self.description}: expected element {self.selector!r} was "
                    "not found in the final page state."
                ),
            )
        return VerificationResult(success=True, reason=self.description)


class SuccessMarkerVerifier(ElementPresentVerifier):
    """Default check for form/multi-step tasks: the fixture's success marker."""

    def __init__(self, selector: str = "#success-marker") -> None:
        super().__init__(
            selector,
            f"Fixture reached its terminal state ({selector} present and visible).",
        )


class AnswerNumberVerifier:
    """EXTRACTION: numeric answer + confidence, compared with a small tolerance."""

    def __init__(self, expected: float, tolerance: float = 1e-6) -> None:
        self.expected = expected
        self.tolerance = tolerance

    async def verify(self, page: Page) -> VerificationResult:
        raw_value, raw_confidence = await _read_answer_fields(page)
        extracted = normalize_number(raw_value)
        if extracted is None:
            return VerificationResult(
                success=False,
                reason=f"No parsable numeric answer was recorded (got {raw_value.strip()!r}).",
                extracted_value=raw_value.strip() or None,
            )
        confidence_problem = _confidence_result(raw_confidence)
        if confidence_problem is not None:
            return confidence_problem
        if abs(extracted - self.expected) > self.tolerance:
            return VerificationResult(
                success=False,
                reason=(
                    f"Extracted value {extracted} differs from expected "
                    f"{self.expected} (tolerance {self.tolerance})."
                ),
                extracted_value=str(extracted),
            )
        return VerificationResult(
            success=True,
            reason=f"Recorded answer matches expected {self.expected}.",
            extracted_value=str(extracted),
        )


class AnswerDateVerifier:
    """EXTRACTION: date answer parsed across common formats + confidence."""

    def __init__(self, expected: date) -> None:
        self.expected = expected

    async def verify(self, page: Page) -> VerificationResult:
        raw_value, raw_confidence = await _read_answer_fields(page)
        parsed = normalize_date(raw_value)
        if parsed is None:
            return VerificationResult(
                success=False,
                reason=f"No parsable date answer was recorded (got {raw_value.strip()!r}).",
                extracted_value=raw_value.strip() or None,
            )
        confidence_problem = _confidence_result(raw_confidence)
        if confidence_problem is not None:
            return confidence_problem
        if parsed != self.expected:
            return VerificationResult(
                success=False,
                reason=(
                    f"Extracted date {parsed.isoformat()} != expected {self.expected.isoformat()}."
                ),
                extracted_value=parsed.isoformat(),
            )
        return VerificationResult(
            success=True,
            reason=f"Recorded date matches expected {self.expected.isoformat()}.",
            extracted_value=parsed.isoformat(),
        )


class AnswerTextVerifier:
    """EXTRACTION: case/space-insensitive exact text match + confidence."""

    def __init__(self, expected: str) -> None:
        self.expected = normalize_text(expected)

    async def verify(self, page: Page) -> VerificationResult:
        raw_value, raw_confidence = await _read_answer_fields(page)
        normalized = normalize_text(raw_value)
        if not normalized:
            return VerificationResult(
                success=False,
                reason="No textual answer was recorded.",
                extracted_value=raw_value.strip() or None,
            )
        confidence_problem = _confidence_result(raw_confidence)
        if confidence_problem is not None:
            return confidence_problem
        if normalized != self.expected:
            return VerificationResult(
                success=False,
                reason=f"Extracted text {normalized!r} != expected {self.expected!r}.",
                extracted_value=normalized,
            )
        return VerificationResult(
            success=True,
            reason=f"Recorded answer matches expected {self.expected!r}.",
            extracted_value=normalized,
        )


# ---------------------------------------------------------------------------
# Registry: Task.verifier_id -> verifier instance.
# ---------------------------------------------------------------------------

#: Generic verifier id used by tasks whose goal is "reach the terminal state".
SUCCESS_MARKER_ID = "verify-success-marker"

VERIFIERS: dict[str, Verifier] = {
    SUCCESS_MARKER_ID: SuccessMarkerVerifier(),
    # navigation/simple_link_01: following the Documentation link lands on docs_01.html
    "verify-nav-docs-reached": ElementPresentVerifier(
        "#target-reached", "Agent followed the Documentation link to docs_01.html."
    ),
    # navigation/dropdown_menu_02: nested dropdown item opened its hidden panel
    "verify-nav-dropdown-open": ElementPresentVerifier(
        '#dropdown-target[data-opened="true"]',
        "Nested dropdown item was opened via the two-level menu.",
    ),
    # navigation/tabs_secret_03: the correct tab out of five similar ones reveals the panel
    "verify-nav-tab-secret": ElementPresentVerifier(
        '#panel-secret[data-opened="true"]',
        "The correct tab was activated and revealed the secret panel.",
    ),
    # extraction/price_01: product price $1,249.90
    "verify-extract-price": AnswerNumberVerifier(expected=1249.90),
    # extraction/date_02: conference date March 14, 2027
    "verify-extract-date": AnswerDateVerifier(expected=date(2027, 3, 14)),
    # extraction/table_count_03: delivered orders with total > 100 (answer: 3)
    "verify-extract-delivered-count": AnswerNumberVerifier(expected=3),
    # error_recovery/dead_end_02: the unobtrusive 'legacy portal' route reaches the goal
    "verify-err-legacy-reached": ElementPresentVerifier(
        "#legacy-goal",
        "The Legacy Reports section was reached after recovering from the dead end.",
    ),
    # distractor/links_02: only the real dashboard link reaches the genuine dashboard
    "verify-distractor-dashboard": ElementPresentVerifier(
        "#dashboard-real",
        "The genuine dashboard page was reached (not one of the look-alike decoys).",
    ),
}
