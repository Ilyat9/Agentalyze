"""The task registry: an explicit, hand-designed list of benchmark tasks.

Tasks are deliberately written by hand (not generated from templates): each
one targets a specific failure mode and should stay individually auditable.
Every task docstring/comment states what it is supposed to reveal about an
agent.
"""

from __future__ import annotations

from agentalyze.analysis.failure_taxonomy import FailureTag
from agentalyze.tasks.models import Task, TaskCategory

C = TaskCategory
#: Generic verifier id for "reach the fixture's terminal state" tasks (see verifiers.py).
MARKER = "verify-success-marker"

TASKS: list[Task] = [
    # ----------------------------- NAVIGATION -----------------------------
    Task(
        id="nav-simple-link-01",
        category=C.NAVIGATION,
        title="Follow the documentation link",
        description=(
            "Open the Acme portal start page. Find the link that leads to the "
            "Documentation section and navigate to it."
        ),
        fixture_path="navigation/simple_link_01.html",
        fixture_url_path="/navigation/simple_link_01.html",
        verifier_id="verify-nav-docs-reached",
        max_steps=5,
        timeout_seconds=60,
        difficulty="easy",
        tags=["links", "single-page"],
        expected_failure_modes=[FailureTag.WRONG_TOOL_CHOICE, FailureTag.HALLUCINATED_ELEMENT],
    ),  # Reveals: can the agent map a human goal ("Documentation") onto the right link at all?
    Task(
        id="nav-dropdown-menu-02",
        category=C.NAVIGATION,
        title="Open item via two-level dropdown menu",
        description=(
            "On the Acme product site, open the Products dropdown menu and go to "
            "the 'Analytics Pro' product page."
        ),
        fixture_path="navigation/dropdown_menu_02.html",
        fixture_url_path="/navigation/dropdown_menu_02.html",
        verifier_id="verify-nav-dropdown-open",
        max_steps=10,
        timeout_seconds=90,
        difficulty="medium",
        tags=["menus", "hover", "multi-click"],
        expected_failure_modes=[FailureTag.HALLUCINATED_ELEMENT, FailureTag.PREMATURE_DONE],
    ),  # Reveals: whether the agent understands hidden menus (submenu exists only after opening the parent item) or keeps clicking a link that is not in the DOM yet.
    Task(
        id="nav-tabs-secret-03",
        category=C.NAVIGATION,
        title="Find the archive tab",
        description=(
            "The settings page has several tabs. Open the Archive tab — it "
            "contains a secret panel that must be revealed."
        ),
        fixture_path="navigation/tabs_secret_03.html",
        fixture_url_path="/navigation/tabs_secret_03.html",
        verifier_id="verify-nav-tab-secret",
        max_steps=12,
        timeout_seconds=90,
        difficulty="hard",
        tags=["tabs", "hidden-content"],
        expected_failure_modes=[FailureTag.GRACEFUL_GIVE_UP, FailureTag.STEP_BUDGET_EXCEEDED_STUCK],
    ),  # Reveals: does the agent systematically try tabs/labels instead of declaring failure when the needed content is initially invisible?
    Task(
        id="nav-breadcrumb-04",
        category=C.NAVIGATION,
        title="Reach the Guides hub via breadcrumbs",
        description=(
            "You are on the 'Deploying to staging' docs page. Get to the Guides "
            "hub of this documentation site."
        ),
        fixture_path="navigation/breadcrumb_04.html",
        fixture_url_path="/navigation/breadcrumb_04.html",
        verifier_id=MARKER,
        max_steps=8,
        timeout_seconds=90,
        difficulty="medium",
        tags=["breadcrumbs", "structural-navigation"],
        expected_failure_modes=[FailureTag.WRONG_TOOL_CHOICE, FailureTag.GRACEFUL_GIVE_UP],
    ),  # Reveals: whether the agent uses structural navigation elements (breadcrumb trail) instead of scanning body links; every sidebar link is a plausible-looking dead end.
    Task(
        id="nav-pagination-05",
        category=C.NAVIGATION,
        title="Find an entry on a later page",
        description=(
            "Open the audit log entry for ticket #4821 ('Refund issued') and view it. "
            "The log is paginated."
        ),
        fixture_path="navigation/pagination_05.html",
        fixture_url_path="/navigation/pagination_05.html",
        verifier_id=MARKER,
        max_steps=15,
        timeout_seconds=120,
        difficulty="medium",
        tags=["pagination", "sequential-state"],
        expected_failure_modes=[FailureTag.LOOPING, FailureTag.PREMATURE_DONE],
    ),  # Reveals: sequential state progression through pagination — agents that give up when the target is absent from page 1, or click Next forever past its end, fail here.

    # ----------------------------- FORM_FILL --------------------------------
    Task(
        id="form-fill-basic-01",
        category=C.FORM_FILL,
        title="Fill the contact form",
        description=(
            "Fill in the support contact form: name 'Ivan Petrov', email "
            "'ivan@example.com', message 'My order has not arrived'. Submit it."
        ),
        fixture_path="form_fill/basic_01.html",
        fixture_url_path="/form_fill/basic_01.html",
        verifier_id=MARKER,
        max_steps=8,
        timeout_seconds=90,
        difficulty="easy",
        tags=["forms"],
        expected_failure_modes=[FailureTag.PREMATURE_DONE, FailureTag.HALLUCINATED_ELEMENT],
    ),  # Reveals: baseline ability to fill several inputs and actually submit (vs. stopping after typing).
    Task(
        id="form-fill-validation-02",
        category=C.FORM_FILL,
        title="Pass client-side validation on registration",
        description=(
            "Register an account: username 'agent_fan', email 'agent@example.org', "
            "password 'S3cure-pass!' (confirm it too), age 30, accept the terms, submit."
        ),
        fixture_path="form_fill/validation_02.html",
        fixture_url_path="/form_fill/validation_02.html",
        verifier_id=MARKER,
        max_steps=15,
        timeout_seconds=120,
        difficulty="medium",
        tags=["forms", "validation"],
        expected_failure_modes=[FailureTag.TOOL_ERROR_MISHANDLED, FailureTag.LOOPING],
    ),  # Reveals: whether the agent reads validation errors (min length, password mismatch, checkbox required) and corrects input instead of resubmitting the same broken state.
    Task(
        id="form-fill-dependent-selects-03",
        category=C.FORM_FILL,
        title="Dependent country/city selects",
        description=(
            "Place a delivery order: choose country Kazakhstan, then city Almaty in "
            "the city selector, accept the terms and submit."
        ),
        fixture_path="form_fill/dependent_selects_03.html",
        fixture_url_path="/form_fill/dependent_selects_03.html",
        verifier_id=MARKER,
        max_steps=15,
        timeout_seconds=120,
        difficulty="hard",
        tags=["forms", "dynamic-dom"],
        expected_failure_modes=[FailureTag.HALLUCINATED_ELEMENT, FailureTag.LOOPING],
    ),  # Reveals: can the agent handle dependent controls (city options exist only after choosing a country) or does it try to select a city before the option is rendered?
    Task(
        id="form-fill-repeater-04",
        category=C.FORM_FILL,
        title="Add two expense lines to a repeater",
        description=(
            "Fill the monthly expense report: add exactly two expense lines — "
            "'Taxi' for 25.50 and 'Hotel' for 180.00 — then submit the report. "
            "New lines must be added with the 'Add expense line' button."
        ),
        fixture_path="form_fill/repeater_04.html",
        fixture_url_path="/form_fill/repeater_04.html",
        verifier_id=MARKER,
        max_steps=20,
        timeout_seconds=150,
        difficulty="hard",
        tags=["forms", "dynamic-dom", "repeater"],
        expected_failure_modes=[
            FailureTag.HALLUCINATED_ELEMENT,
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING,
        ],
    ),  # Reveals: interacting with rows that do not exist until dynamically added — the agent must re-observe after each DOM mutation instead of filling inputs it merely assumes will appear.
    Task(
        id="form-edit-prefilled-05",
        category=C.FORM_FILL,
        title="Edit only what must change",
        description=(
            "The profile form is pre-filled with current data. Change the email to "
            "'maria@new.example.com' and the plan to Pro, keep everything else as-is, "
            "and save."
        ),
        fixture_path="form_fill/edit_prefilled_05.html",
        fixture_url_path="/form_fill/edit_prefilled_05.html",
        verifier_id=MARKER,
        max_steps=12,
        timeout_seconds=120,
        difficulty="medium",
        tags=["forms", "selective-editing"],
        expected_failure_modes=[FailureTag.PREMATURE_DONE, FailureTag.WRONG_TOOL_CHOICE],
    ),  # Reveals: selective editing without clobbering existing data — over-eager agents that rewrite or clear pre-filled fields (the immutable name) are rejected by the page.

    # ----------------------------- EXTRACTION -------------------------------
    # EXTRACTION tasks use an answer form: the agent writes its answer AND a
    # confidence label (0..1) into the page, so success stays DOM-checkable.
    Task(
        id="extract-price-01",
        category=C.EXTRACTION,
        title="Extract the product price",
        description=(
            "Find the price of the 'Nordic Chair' on the page. Record your answer "
            "in USD as a plain number (e.g. 12.34) in the Answer field together "
            "with your confidence between 0 and 1, then submit."
        ),
        fixture_path="extraction/price_01.html",
        fixture_url_path="/extraction/price_01.html",
        verifier_id="verify-extract-price",
        max_steps=8,
        timeout_seconds=90,
        difficulty="easy",
        tags=["extraction", "numbers", "confidence"],
        expected_failure_modes=[FailureTag.PREMATURE_DONE, FailureTag.GRACEFUL_GIVE_UP],
    ),  # Reveals: reading a value formatted as '$1,249.90' and normalising it to 1249.90; also whether the agent reports calibrated confidence instead of skipping it.
    Task(
        id="extract-date-02",
        category=C.EXTRACTION,
        title="Extract the conference date",
        description=(
            "The page announces a conference. Find its start date and record it in "
            "the Answer field in YYYY-MM-DD format with a confidence value "
            "between 0 and 1, then submit."
        ),
        fixture_path="extraction/date_02.html",
        fixture_url_path="/extraction/date_02.html",
        verifier_id="verify-extract-date",
        max_steps=10,
        timeout_seconds=90,
        difficulty="medium",
        tags=["extraction", "dates", "confidence"],
        expected_failure_modes=[FailureTag.HALLUCINATED_ELEMENT, FailureTag.GRACEFUL_GIVE_UP],
    ),  # Reveals: extracting a date written in prose ('March 14, 2027') and reformatting to ISO; distractor dates (registration deadline, afterparty) test precision.
    Task(
        id="extract-table-count-03",
        category=C.EXTRACTION,
        title="Count qualifying orders in a table",
        description=(
            "The page shows an order table. Count the orders that are BOTH "
            "delivered AND have a total greater than 100.00. Record the number "
            "in the Answer field with your confidence between 0 and 1, then submit."
        ),
        fixture_path="extraction/table_count_03.html",
        fixture_url_path="/extraction/table_count_03.html",
        verifier_id="verify-extract-delivered-count",
        max_steps=15,
        timeout_seconds=150,
        difficulty="hard",
        tags=["extraction", "tables", "aggregation", "confidence"],
        expected_failure_modes=[
            FailureTag.WRONG_TOOL_CHOICE,
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING,
        ],
    ),  # Reveals: multi-row filtering on two conditions (status + amount) with near-threshold decoys (delivered but <=100, >100 but not delivered) — catches sloppy scanning and arithmetic slips.
    Task(
        id="extract-order-total-04",
        category=C.EXTRACTION,
        title="Compute today's charge from several page areas",
        description=(
            "On the order review page, work out how much will be charged to the card "
            "TODAY (hardware plus one-time fees only). Record your answer in USD as "
            "a plain number in the Answer field with your confidence between 0 and 1, "
            "then submit."
        ),
        fixture_path="extraction/order_total_04.html",
        fixture_url_path="/extraction/order_total_04.html",
        verifier_id="verify-extract-today-total",
        max_steps=15,
        timeout_seconds=150,
        difficulty="hard",
        tags=["extraction", "arithmetic", "multi-area", "confidence"],
        expected_failure_modes=[FailureTag.PREMATURE_DONE, FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING],
    ),  # Reveals: combining values from multiple places on one page ($129.99 body + $45.00 one-time warranty) into a derived number that appears nowhere verbatim — the monthly subscription is an arithmetic distractor.
    Task(
        id="extract-api-version-05",
        category=C.EXTRACTION,
        title="Extract the current stable API version",
        description=(
            "Find the current stable version of the Acme public API and record it "
            "in the Answer field (version string) with your confidence between 0 "
            "and 1, then submit."
        ),
        fixture_path="extraction/api_version_05.html",
        fixture_url_path="/extraction/api_version_05.html",
        verifier_id="verify-extract-api-version",
        max_steps=10,
        timeout_seconds=120,
        difficulty="medium",
        tags=["extraction", "label-value", "confidence"],
        expected_failure_modes=[FailureTag.HALLUCINATED_ELEMENT, FailureTag.PREMATURE_DONE],
    ),  # Reveals: label-value extraction from a definition list where prose around it offers distractor versions (minimum supported 1.9.0, beta 3.0.0-rc2) — tests precise attribute binding.
    # ----------------------------- MULTI_STEP ---------------------------------
    Task(
        id="multi-shop-checkout-01",
        category=C.MULTI_STEP,
        title="Add item to cart and check out",
        description=(
            "In the mini-shop, add the 'Bamboo Desk Lamp' to the cart, open the "
            "cart, accept the checkout terms and complete the purchase."
        ),
        fixture_path="multi_step/shop_checkout_01.html",
        fixture_url_path="/multi_step/shop_checkout_01.html",
        verifier_id=MARKER,
        max_steps=20,
        timeout_seconds=180,
        difficulty="medium",
        tags=["multi-step", "shopping"],
        expected_failure_modes=[
            FailureTag.LOOPING,
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING,
        ],
    ),  # Reveals: state tracking across views (list -> cart -> confirmation) and whether the agent adds the right item among several products.
    Task(
        id="multi-wizard-02",
        category=C.MULTI_STEP,
        title="Complete the three-step wizard",
        description=(
            "Complete the account setup wizard: step 1 — name 'Olga Ivanova'; "
            "step 2 — choose the Pro plan; step 3 — confirm and finish."
        ),
        fixture_path="multi_step/wizard_02.html",
        fixture_url_path="/multi_step/wizard_02.html",
        verifier_id=MARKER,
        max_steps=25,
        timeout_seconds=180,
        difficulty="hard",
        tags=["multi-step", "wizard"],
        expected_failure_modes=[FailureTag.PREMATURE_DONE, FailureTag.LOOPING],
    ),  # Reveals: sequential form handling where each step gates the next (Next is inert until the current step is valid) — catches agents that try to jump to the end.
    Task(
        id="multi-settings-toggles-03",
        category=C.MULTI_STEP,
        title="Enable settings and save",
        description=(
            "In the settings panel turn ON email notifications and beta features, "
            "then save the settings."
        ),
        fixture_path="multi_step/settings_toggles_03.html",
        fixture_url_path="/multi_step/settings_toggles_03.html",
        verifier_id=MARKER,
        max_steps=12,
        timeout_seconds=120,
        difficulty="medium",
        tags=["multi-step", "toggles"],
        expected_failure_modes=[FailureTag.LOOPING, FailureTag.TOOL_ERROR_MISHANDLED],
    ),  # Reveals: whether the agent notices toggle states are already partially ON and avoids double-toggling them OFF before saving.
    Task(
        id="multi-transfer-confirm-04",
        category=C.MULTI_STEP,
        title="Confirm a transfer with a one-time code",
        description=(
            "Send $250 to account ACC-778812 using the transfer page. Complete "
            "every confirmation step the site requires."
        ),
        fixture_path="multi_step/transfer_confirm_04.html",
        fixture_url_path="/multi_step/transfer_confirm_04.html",
        verifier_id=MARKER,
        max_steps=18,
        timeout_seconds=180,
        difficulty="hard",
        tags=["multi-step", "memory-across-views", "confirmation"],
        expected_failure_modes=[
            FailureTag.HALLUCINATED_ELEMENT,
            FailureTag.STEP_BUDGET_EXCEEDED_STUCK,
        ],
    ),  # Reveals: carrying information across views — the confirmation code exists only on the review screen; agents that never read it there hallucinate or guess it on the next screen.
    Task(
        id="multi-search-filter-05",
        category=C.MULTI_STEP,
        title="Find a product via live search",
        description=(
            "In the hardware catalog, find the 'Copper Kettle Classic' using the "
            "search box and open its details."
        ),
        fixture_path="multi_step/search_filter_05.html",
        fixture_url_path="/multi_step/search_filter_05.html",
        verifier_id=MARKER,
        max_steps=14,
        timeout_seconds=150,
        difficulty="medium",
        tags=["multi-step", "live-filtering"],
        expected_failure_modes=[FailureTag.WRONG_TOOL_CHOICE, FailureTag.STEP_BUDGET_EXCEEDED_STUCK],
    ),  # Reveals: type-driven dynamic filtering — after typing, the DOM changes and stale element ids vanish; the agent must re-observe before clicking instead of targeting elements it saw earlier.
    # ----------------------------- ERROR_RECOVERY -----------------------------
    Task(
        id="err-retry-submit-01",
        category=C.ERROR_RECOVERY,
        title="Retry after rejected submission",
        description=(
            "Submit a support ticket with the title 'Printer on fire'. If the "
            "submission is rejected, retry until it goes through."
        ),
        fixture_path="error_recovery/retry_submit_01.html",
        fixture_url_path="/error_recovery/retry_submit_01.html",
        verifier_id=MARKER,
        max_steps=12,
        timeout_seconds=120,
        difficulty="easy",
        tags=["error-recovery", "forms"],
        expected_failure_modes=[FailureTag.GRACEFUL_GIVE_UP, FailureTag.LOOPING],
    ),  # Reveals: recovery from a deliberate first-attempt rejection (server error banner): does the agent simply retry instead of concluding the site is broken or changing valid data?
    Task(
        id="err-dead-end-02",
        category=C.ERROR_RECOVERY,
        title="Recover from a dead-end link",
        description=(
            "Reach the Legacy Reports section of this portal starting from the "
            "home page. If a path turns out to be broken, find another way."
        ),
        fixture_path="error_recovery/dead_end_02.html",
        fixture_url_path="/error_recovery/dead_end_02.html",
        verifier_id="verify-err-legacy-reached",
        max_steps=18,
        timeout_seconds=180,
        difficulty="hard",
        tags=["error-recovery", "navigation"],
        expected_failure_modes=[
            FailureTag.GRACEFUL_GIVE_UP,
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING,
        ],
    ),  # Reveals: backtracking after hitting an error page behind the prominent 'Continue' link; the real path ('legacy portal' footer link) requires exploration, not giving up.
    Task(
        id="err-flaky-widget-03",
        category=C.ERROR_RECOVERY,
        title="Generate a flaky report",
        description=(
            "Use the page to generate the monthly report. The generator may fail "
            "sporadically — keep trying until the report is actually produced."
        ),
        fixture_path="error_recovery/flaky_widget_03.html",
        fixture_url_path="/error_recovery/flaky_widget_03.html",
        verifier_id=MARKER,
        max_steps=15,
        timeout_seconds=150,
        difficulty="hard",
        tags=["error-recovery", "flaky"],
        expected_failure_modes=[FailureTag.GRACEFUL_GIVE_UP, FailureTag.LOOPING],
    ),  # Reveals: persistence under repeated transient failures (two deliberate failures with different messages, third attempt works) — catches agents that give up or loop without re-clicking.
    Task(
        id="err-slow-export-04",
        category=C.ERROR_RECOVERY,
        title="Wait out a stalled export",
        description=(
            "Export your account data from this page and keep going until the "
            "archive is actually ready."
        ),
        fixture_path="error_recovery/slow_export_04.html",
        fixture_url_path="/error_recovery/slow_export_04.html",
        verifier_id=MARKER,
        max_steps=15,
        timeout_seconds=120,
        difficulty="medium",
        tags=["error-recovery", "stall", "patience"],
        expected_failure_modes=[FailureTag.LOOPING, FailureTag.STEP_BUDGET_EXCEEDED_STUCK],
    ),  # Reveals: recovering from a network-like hang — after 'Start export' the page stalls ~2.5s with no feedback; agents must wait (wait_for) for completion instead of re-clicking a disabled button or declaring failure early.
    Task(
        id="err-misleading-error-05",
        category=C.ERROR_RECOVERY,
        title="Save contact details despite a wrong error message",
        description=(
            "Update the contact details on this page so they save successfully. "
            "If saving is rejected, make it go through without inventing new data."
        ),
        fixture_path="error_recovery/misleading_error_05.html",
        fixture_url_path="/error_recovery/misleading_error_05.html",
        verifier_id=MARKER,
        max_steps=18,
        timeout_seconds=150,
        difficulty="hard",
        tags=["error-recovery", "misleading-error", "diagnosis"],
        expected_failure_modes=[FailureTag.LOOPING, FailureTag.TOOL_ERROR_MISHANDLED],
    ),  # Reveals: diagnosing a MISATTRIBUTED error — the banner blames the email field while the real problem is an incomplete phone number; resubmitting unchanged data or blindly rewriting the email loops forever.
    # ----------------------------- DISTRACTOR ---------------------------------
    Task(
        id="distractor-buttons-01",
        category=C.DISTRACTOR,
        title="Pick the real submit button",
        description=(
            "Fill the Payload field with 'ok' and submit the form using the "
            "correct submit button on the page."
        ),
        fixture_path="distractor/buttons_01.html",
        fixture_url_path="/distractor/buttons_01.html",
        verifier_id=MARKER,
        max_steps=10,
        timeout_seconds=120,
        difficulty="easy",
        tags=["distractors", "forms", "precision"],
        expected_failure_modes=[FailureTag.HALLUCINATED_ELEMENT, FailureTag.WRONG_TOOL_CHOICE],
    ),  # Reveals: distinguishing the real 'Submit' from a disabled 'Submit' (real disabled attribute), a fake 'Submit form' that resets the form, and an unlabeled icon button.
    Task(
        id="distractor-links-02",
        category=C.DISTRACTOR,
        title="Find the genuine dashboard link",
        description=(
            "Several links on this page claim to lead to the dashboard. Find and "
            "follow the one that leads to the genuine dashboard."
        ),
        fixture_path="distractor/links_02.html",
        fixture_url_path="/distractor/links_02.html",
        verifier_id="verify-distractor-dashboard",
        max_steps=12,
        timeout_seconds=150,
        difficulty="medium",
        tags=["distractors", "links", "precision"],
        expected_failure_modes=[FailureTag.WRONG_TOOL_CHOICE, FailureTag.PREMATURE_DONE],
    ),  # Reveals: text-similarity traps ('Open Dashboard', 'Open Dashboard v2', 'Legacy dashboard'...) where only one target is correct — measures selection precision, not raw clicking ability.
    Task(
        id="distractor-forms-03",
        category=C.DISTRACTOR,
        title="Use the working form",
        description=(
            "This page has two identical-looking registration forms. Fill in name "
            "'Test User', email 'user@example.com' and submit using the form "
            "that actually works."
        ),
        fixture_path="distractor/forms_03.html",
        fixture_url_path="/distractor/forms_03.html",
        verifier_id=MARKER,
        max_steps=12,
        timeout_seconds=150,
        difficulty="hard",
        tags=["distractors", "forms", "disabled-elements"],
        expected_failure_modes=[FailureTag.TOOL_ERROR_MISHANDLED, FailureTag.STEP_BUDGET_EXCEEDED_STUCK],
    ),  # Reveals: noticing that one of two visually identical forms is inert (fieldset disabled + aria-disabled) and switching to the working one instead of fighting the decoy.
    Task(
        id="distractor-checkbox-negation-04",
        category=C.DISTRACTOR,
        title="Configure email preferences with negated labels",
        description=(
            "On the email preferences page, set up the account to receive the "
            "weekly digest and security alerts, but NOT promotional offers, then "
            "save. Read every option carefully."
        ),
        fixture_path="distractor/checkbox_negation_04.html",
        fixture_url_path="/distractor/checkbox_negation_04.html",
        verifier_id=MARKER,
        max_steps=12,
        timeout_seconds=120,
        difficulty="medium",
        tags=["distractors", "checkboxes", "negation"],
        expected_failure_modes=[FailureTag.TOOL_ERROR_MISHANDLED, FailureTag.PREMATURE_DONE],
    ),  # Reveals: deceptive NEGATED checkbox labels ("Do NOT send me…") whose required end states invert naive label matching — plus a destructive-looking 'unsubscribe from ALL emails' trap box.
    Task(
        id="distractor-readmore-context-05",
        category=C.DISTRACTOR,
        title="Open the genuine privacy policy update",
        description=(
            "This news page announces a privacy policy update. Open the page where "
            "its full text can actually be read."
        ),
        fixture_path="distractor/readmore_context_05.html",
        fixture_url_path="/distractor/readmore_context_05.html",
        verifier_id="verify-distractor-privacy-reached",
        max_steps=10,
        timeout_seconds=120,
        difficulty="medium",
        tags=["distractors", "links", "context-selection"],
        expected_failure_modes=[FailureTag.WRONG_TOOL_CHOICE, FailureTag.LOOPING],
    ),  # Reveals: selecting among links with IDENTICAL text ('Read more' x5) where only the surrounding card context disambiguates — unlike text-similarity traps, link text itself carries zero signal.
]

#: Convenience lookup by task id.
TASKS_BY_ID: dict[str, Task] = {task.id: task for task in TASKS}
