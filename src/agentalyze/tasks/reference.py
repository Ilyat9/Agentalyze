"""Reference data for fixture validation -- NOT part of the public task API.

This module maps every task id to the selectors/values that a *programmatic*
(Playwright, no agent) driver needs to prove the fixture is solvable. It
exists so the task-suite author can verify each fixture end-to-end before any
real agent touches it.

**This data must never reach the agent.** ``Task`` (the public API) carries no
selectors; handing these to an agent would turn the benchmark into a
fill-in-the-blank exercise instead of a realistic web-interaction eval.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Actions the programmatic fixture driver knows how to perform.
ReferenceAction = Literal["fill", "click", "select", "check"]


class ReferenceStep(BaseModel):
    """One programmatic interaction step used only by fixture validation."""

    action: ReferenceAction
    selector: str
    value: str | None = Field(
        default=None,
        description="Value for 'fill'/'select'; ignored otherwise.",
    )


class TaskReference(BaseModel):
    """How to drive one fixture to its success marker without an agent."""

    #: Selector proving the terminal state was reached.
    success_selector: str
    #: Steps that lead there when executed in order against the served fixture.
    steps: list[ReferenceStep]


def _ref(
    success_selector: str, *steps: tuple[ReferenceAction, str, str | None]
) -> TaskReference:
    """Small factory keeping the registry readable."""
    return TaskReference(
        success_selector=success_selector,
        steps=[ReferenceStep(action=a, selector=s, value=v) for a, s, v in steps],
    )


REFERENCE: dict[str, TaskReference] = {
    # --- NAVIGATION ---------------------------------------------------------
    "nav-simple-link-01": _ref("#target-reached", ("click", 'a[data-nav="docs"]', None)),
    "nav-dropdown-menu-02": _ref(
        '#dropdown-target[data-opened="true"]',
        ("click", 'button[data-menu-toggle="products"]', None),
        ("click", 'button[data-submenu-item="analytics-pro"]', None),
    ),
    "nav-tabs-secret-03": _ref(
        '#panel-secret[data-opened="true"]',
        ("click", 'button[data-tab-id="tab-archive"]', None),
    ),
    "nav-breadcrumb-04": _ref(
        "#success-marker",
        ("click", 'a[data-crumb="guides"]', None),
    ),
    "nav-pagination-05": _ref(
        "#success-marker",
        ("click", "#next-page", None),
        ("click", "#next-page", None),  # ticket #4821 lives on page 3
        ("click", 'button[data-view-ticket="4821"]', None),
    ),
    # --- FORM_FILL ------------------------------------------------------------
    "form-fill-basic-01": _ref(
        "#success-marker",
        ("fill", "#name", "Ivan Petrov"),
        ("fill", "#email", "ivan@example.com"),
        ("fill", "#message", "Hello from validation."),
        ("click", "#submit-btn", None),
    ),
    "form-fill-validation-02": _ref(
        "#success-marker",
        ("fill", "#username", "agent_fan"),
        ("fill", "#email", "agent@example.org"),
        ("fill", "#password", "S3cure-pass!"),
        ("fill", "#password-confirm", "S3cure-pass!"),
        ("fill", "#age", "30"),
        ("check", "#terms", None),
        ("click", "#register-btn", None),
    ),
    "form-fill-dependent-selects-03": _ref(
        "#success-marker",
        ("select", "#country", "kz"),
        ("select", "#city", "almaty"),
        ("check", "#terms", None),
        ("click", "#order-btn", None),
    ),
    "form-fill-repeater-04": _ref(
        "#success-marker",
        # Rows exist in the DOM only after each 'Add expense line' click.
        ("click", "#add-line", None),
        ("fill", '.expense-row[data-line-index="0"] .line-desc', "Taxi"),
        ("fill", '.expense-row[data-line-index="0"] .line-amount', "25.50"),
        ("click", "#add-line", None),
        ("fill", '.expense-row[data-line-index="1"] .line-desc', "Hotel"),
        ("fill", '.expense-row[data-line-index="1"] .line-amount', "180.00"),
        ("click", "#submit-expenses", None),
    ),
    "form-edit-prefilled-05": _ref(
        "#success-marker",
        # Name arrives pre-filled and must stay untouched; only email + plan change.
        ("fill", "#email", "maria@new.example.com"),
        ("select", "#plan", "pro"),
        ("click", "#save-profile", None),
    ),
}

# --- EXTRACTION -------------------------------------------------------------
# Answer-form pattern: the agent records its answer and a confidence label.
REFERENCE["extract-price-01"] = _ref(
    "#answer-recorded",
    ("fill", "#answer-value", "1249.90"),
    ("fill", "#answer-confidence", "0.95"),
    ("click", "#submit-answer", None),
)

REFERENCE["extract-date-02"] = _ref(
    "#answer-recorded",
    ("fill", "#answer-value", "2027-03-14"),
    ("fill", "#answer-confidence", "0.9"),
    ("click", "#submit-answer", None),
)

REFERENCE["extract-table-count-03"] = _ref(
    "#answer-recorded",
    ("fill", "#answer-value", "3"),
    ("fill", "#answer-confidence", "0.8"),
    ("click", "#submit-answer", None),
)

# 129.99 (camera body) + 45.00 (one-time warranty) = 174.99 charged today;
# the monthly subscription is explicitly NOT charged today.
REFERENCE["extract-order-total-04"] = _ref(
    "#answer-recorded",
    ("fill", "#answer-value", "174.99"),
    ("fill", "#answer-confidence", "0.9"),
    ("click", "#submit-answer", None),
)

REFERENCE["extract-api-version-05"] = _ref(
    "#answer-recorded",
    ("fill", "#answer-value", "2.4.1"),
    ("fill", "#answer-confidence", "0.95"),
    ("click", "#submit-answer", None),
)

# --- MULTI_STEP ---------------------------------------------------------------
REFERENCE["multi-shop-checkout-01"] = _ref(
    "#success-marker",
    ("click", 'button[data-add-to-cart="item-bamboo"]', None),
    ("click", 'a[data-nav="cart"]', None),
    ("check", "#confirm-terms", None),
    ("click", "#checkout-btn", None),
)

REFERENCE["multi-wizard-02"] = _ref(
    "#success-marker",
    ("fill", "#wiz-name", "Olga Ivanova"),
    ("click", "#wiz-next-1", None),
    ("select", "#wiz-plan", "pro"),
    ("click", "#wiz-next-2", None),
    ("check", "#wiz-confirm", None),
    ("click", "#wiz-finish", None),
)

REFERENCE["multi-settings-toggles-03"] = _ref(
    "#success-marker",
    # notifications starts ON by design; only beta needs enabling
    ("click", "#toggle-beta", None),
    ("click", "#save-settings", None),
)

REFERENCE["multi-transfer-confirm-04"] = _ref(
    "#success-marker",
    ("fill", "#recipient", "ACC-778812"),
    ("fill", "#amount", "250"),
    ("click", "#review-btn", None),
    # The code (4827) is only displayed on the review screen reached above.
    ("fill", "#confirm-code", "4827"),
    ("click", "#confirm-btn", None),
)

REFERENCE["multi-search-filter-05"] = _ref(
    "#success-marker",
    ("fill", "#catalog-search", "copper kettle"),
    ("click", 'button[data-details="copper-kettle-classic"]', None),
)

# --- ERROR_RECOVERY -----------------------------------------------------------
REFERENCE["err-retry-submit-01"] = _ref(
    "#success-marker",
    ("fill", "#ticket-title", "Printer on fire"),
    ("click", "#submit-ticket", None),  # attempt 1 fails by design
    ("click", "#submit-ticket", None),  # attempt 2 succeeds
)

REFERENCE["err-dead-end-02"] = _ref(
    "#legacy-goal",
    ("click", 'a[data-link="big-continue"]', None),  # dead end by design
    ("click", 'a[data-link="back-home"]', None),
    ("click", 'a[data-link="legacy-portal"]', None),
)

REFERENCE["err-flaky-widget-03"] = _ref(
    "#success-marker",
    ("click", "#generate-report", None),  # fails twice by design...
    ("click", "#generate-report", None),
    ("click", "#generate-report", None),  # ...third click works
)

# One click, then patience: the export "hangs" for 2.5s and completes by itself.
REFERENCE["err-slow-export-04"] = _ref(
    '#export-done[data-done="true"]',
    ("click", "#start-export", None),
)

# First submit fails with a banner that wrongly blames the email field;
# the real fix is completing the phone number.
REFERENCE["err-misleading-error-05"] = _ref(
    "#success-marker",
    ("click", "#save-contact", None),  # rejected by design (misleading banner)
    ("fill", "#phone", "+1 555 010 20 30"),
    ("click", "#save-contact", None),
)

# --- DISTRACTOR -----------------------------------------------------------------
REFERENCE["distractor-buttons-01"] = _ref(
    "#success-marker",
    ("fill", "#payload", "ok"),
    ("click", "#real-submit", None),
)

REFERENCE["distractor-links-02"] = _ref(
    "#dashboard-real",
    ("click", 'a[data-dashboard="genuine-v3"]', None),
)

REFERENCE["distractor-forms-03"] = _ref(
    "#success-marker",
    ("fill", "#real-name", "Test User"),
    ("fill", "#real-email", "user@example.com"),
    ("click", "#real-submit", None),
)

# Required end state: digest ON, security ON, promo OFF. Labels are negated,
# so the only checkbox that must be TOGGLED is "Do NOT send me promotional offers";
# the destructive-looking unsubscribe-all box must stay untouched.
REFERENCE["distractor-checkbox-negation-04"] = _ref(
    "#success-marker",
    ("check", "#opt-promo", None),
    ("click", "#save-prefs", None),
)

REFERENCE["distractor-readmore-context-05"] = _ref(
    '#privacy-details[data-reached="true"]',
    ("click", 'a[data-readmore="privacy-update"]', None),
)
