"""Unit tests for every verifier: one success and one failure scenario each.

These tests drive real pages (via Playwright) into known states — either the
fixture's terminal state or its initial state — and then call the verifier.
They are NOT agent runs: the page is driven programmatically.

Requires Chromium: marked ``browser`` (excluded from the default pytest run).
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("playwright", reason="requires the 'browser' extra")

from agentalyze.tasks.models import VerificationResult
from agentalyze.tasks.verifiers import (
    VERIFIERS,
    AnswerDateVerifier,
    AnswerNumberVerifier,
    AnswerTextVerifier,
    ElementPresentVerifier,
    SuccessMarkerVerifier,
)

pytestmark = pytest.mark.browser


ANSWER_PAGE_TEMPLATE = """
<html><body>
  <span id="recorded-answer">{value}</span>
  <span id="recorded-confidence">{confidence}</span>
</body></html>
"""


async def set_answer(page, value: str, confidence: str) -> None:
    await page.set_content(
        ANSWER_PAGE_TEMPLATE.format(value=value, confidence=confidence), wait_until="load"
    )


# --- SuccessMarkerVerifier / ElementPresentVerifier ---------------------------


async def test_success_marker_success(page) -> None:
    await page.set_content('<div id="success-marker" data-success="true">Done.</div>')
    result = await SuccessMarkerVerifier().verify(page)
    assert result.success is True
    assert "terminal state" in result.reason


async def test_success_marker_failure_on_initial_state(page) -> None:
    # Initial fixture state: the marker is absent (or still hidden).
    await page.set_content("<html><body><form></form></body></html>")
    result = await SuccessMarkerVerifier().verify(page)
    assert result.success is False
    assert "#success-marker" in result.reason


async def test_element_present_verifier_attached_mode(page) -> None:
    verifier = ElementPresentVerifier("#hidden-target", "Hidden target attached.", visible=False)
    await page.set_content('<div id="hidden-target" hidden>data</div>')
    assert (await verifier.verify(page)).success is True


async def test_element_present_verifier_missing_element_fails(page) -> None:
    verifier = ElementPresentVerifier("#nope", "Expected element.")
    await page.set_content("<p>nothing relevant</p>")
    result = await verifier.verify(page)
    assert result.success is False
    assert "#nope" in result.reason


# --- AnswerNumberVerifier ------------------------------------------------------


async def test_answer_number_accepts_formatted_value_and_confidence(page) -> None:
    await set_answer(page, value="1,249.90", confidence="0.95")
    result = await AnswerNumberVerifier(expected=1249.90).verify(page)
    assert result.success is True
    assert result.extracted_value == "1249.9"


async def test_answer_number_rejects_wrong_value(page) -> None:
    await set_answer(page, value="999", confidence="0.95")
    result = await AnswerNumberVerifier(expected=1249.90).verify(page)
    assert result.success is False
    assert "differs from expected" in result.reason
    assert result.extracted_value == "999.0"


async def test_answer_number_requires_confidence(page) -> None:
    await set_answer(page, value="1249.90", confidence="")
    result = await AnswerNumberVerifier(expected=1249.90).verify(page)
    assert result.success is False
    assert "confidence" in result.reason.lower()


async def test_answer_number_rejects_unparsable_value(page) -> None:
    await set_answer(page, value="not a number", confidence="0.5")
    result = await AnswerNumberVerifier(expected=1).verify(page)
    assert result.success is False
    assert "No parsable numeric answer" in result.reason


# --- AnswerDateVerifier --------------------------------------------------------


async def test_answer_date_accepts_alternate_format(page) -> None:
    await set_answer(page, value="March 14, 2027", confidence="0.9")
    result = await AnswerDateVerifier(expected=date(2027, 3, 14)).verify(page)
    assert result.success is True
    assert result.extracted_value == "2027-03-14"


async def test_answer_date_rejects_wrong_date(page) -> None:
    await set_answer(page, value="2027-03-15", confidence="0.9")
    result = await AnswerDateVerifier(expected=date(2027, 3, 14)).verify(page)
    assert result.success is False
    assert "!= expected" in result.reason


async def test_answer_date_rejects_unparsable_text(page) -> None:
    await set_answer(page, value="sometime next spring", confidence="0.9")
    result = await AnswerDateVerifier(expected=date(2027, 3, 14)).verify(page)
    assert result.success is False
    assert "No parsable date answer" in result.reason


# --- AnswerTextVerifier --------------------------------------------------------


async def test_answer_text_normalizes_case_and_whitespace(page) -> None:
    await set_answer(page, value="  Delivered   to warehouse ", confidence="0.85")
    result = await AnswerTextVerifier(expected="delivered to warehouse").verify(page)
    assert result.success is True
    assert result.extracted_value == "delivered to warehouse"


async def test_answer_text_rejects_mismatch(page) -> None:
    await set_answer(page, value="shipped", confidence="0.85")
    result = await AnswerTextVerifier(expected="delivered").verify(page)
    assert result.success is False
    assert "!= expected" in result.reason


async def test_answer_text_fails_when_nothing_recorded(page) -> None:
    await set_answer(page, value="", confidence="0.5")
    result = await AnswerTextVerifier(expected="anything").verify(page)
    assert result.success is False
    assert "No textual answer was recorded." == result.reason


# --- Registry integrity ---------------------------------------------------------


@pytest.mark.parametrize("verifier_id", sorted(VERIFIERS))
def test_every_registered_verifier_satisfies_protocol(verifier_id: str) -> None:
    """Every named instance in the VERIFIERS registry must be a usable Verifier."""
    from agentalyze.tasks.verifiers import Verifier

    assert isinstance(VERIFIERS[verifier_id], Verifier)


def test_verification_result_defaults() -> None:
    result = VerificationResult(success=False, reason="why")
    assert result.extracted_value is None

