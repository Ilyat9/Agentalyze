"""Per-tag unit tests for the failure taxonomy.

Every test hand-builds a minimal RunTrace whose step sequence is engineered to
trigger exactly one specific tag (or a documented tag combination), then
asserts the classifier's exact output. No browser, no model, milliseconds.
"""

from __future__ import annotations

from agentalyze.analysis.failure_taxonomy import (
    FailureTag,
    classify_failure,
)
from agentalyze.runner.trace import RunOutcome
from tests.analysis.conftest import make_step, make_trace


class TestWrongToolChoice:
    def test_unknown_tool_name(self) -> None:
        # The model invented a tool ("clck" instead of "click"): rule 1.
        steps = [
            make_step(1, "navigate", {"url": "/start.html"}),
            make_step(2, "clck", {"element_id": "e1"}, tool_success=False),
            # done at step 3: beyond the premature-done suspicion window.
            make_step(3, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.WRONG_TOOL_CHOICE]

    def test_finished_without_any_state_changing_tool(self) -> None:
        # FAILURE_VERIFIER where the model only READ (extract/wait) and then
        # declared victory: rule 2 — reading tools cannot complete a task that
        # requires page manipulation.
        steps = [
            make_step(1, "extract_text", {"element_id": "e1"}),
            make_step(2, "wait_for", {"condition_description": "something"}),
            make_step(3, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.WRONG_TOOL_CHOICE]

    def test_read_only_run_on_max_steps_is_not_wrong_tool_choice(self) -> None:
        # Rule 2 is deliberately restricted to verifier failures: a read-only
        # strategy that merely ran out of budget may be a legitimate wait.
        steps = [
            make_step(1, "extract_text", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "wait_for", {"condition_description": "x"}, dom_hash="h1"),
            make_step(3, "extract_text", {"element_id": "e2"}, dom_hash="h1"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        tags = classify_failure(trace)
        assert FailureTag.WRONG_TOOL_CHOICE not in tags


class TestHallucinatedElement:
    def test_action_on_id_absent_from_latest_observation(self) -> None:
        # Latest observation lists e1/e2 only; the model clicks e9.
        steps = [
            make_step(1, "click", {"element_id": "e9"}, tool_success=False),
            make_step(2, "click", {"element_id": "e1"}, dom_hash="h2"),
            # done at step 3 keeps PREMATURE_DONE out of the picture.
            make_step(3, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.HALLUCINATED_ELEMENT]

    def test_no_observed_ids_means_no_judgment(self) -> None:
        # Degenerate observation without any element ids -> skip, no tag.
        steps = [
            make_step(
                1,
                "click",
                {"element_id": "e9"},
                tool_success=False,
                observed_ids=(),
            ),
            make_step(2, "done", {"success": False}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert FailureTag.HALLUCINATED_ELEMENT not in classify_failure(trace)


class TestLooping:
    def test_three_identical_calls_loop(self) -> None:
        args = {"element_id": "e1", "text": "hello"}
        steps = [
            make_step(1, "type_text", args, dom_hash="h1"),
            make_step(2, "type_text", args, dom_hash="h2"),
            make_step(3, "type_text", args, dom_hash="h3"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        # Hashes kept changing right up to the end => budget tag is
        # WHILE_PROGRESSING; LOOPING comes from the 3 identical calls.
        expected = [
            FailureTag.LOOPING,
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING,
        ]
        assert classify_failure(trace) == expected

    def test_two_identical_calls_are_not_a_loop(self) -> None:
        # A single verbatim retry is legitimate; threshold constant is 3.
        args = {"element_id": "e1", "text": "hello"}
        steps = [
            make_step(1, "type_text", args, dom_hash="h1"),
            make_step(2, "type_text", args, dom_hash="h2"),
            make_step(3, "click", {"element_id": "e2"}, dom_hash="h3"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING
        ]

    def test_cosmetic_argument_drift_still_counts_as_loop(self) -> None:
        # Whitespace/case drift must not hide a loop: normalization strips it.
        steps = [
            make_step(1, "type_text", {"element_id": "e1", "text": "Hello"}, dom_hash="h1"),
            make_step(2, "type_text", {"element_id": "e1", "text": " hello "}, dom_hash="h2"),
            make_step(3, "type_text", {"element_id": "e1", "text": "HELLO"}, dom_hash="h3"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert FailureTag.LOOPING in classify_failure(trace)

class TestBudgetExceeded:
    def test_progressing_when_hashes_kept_changing(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h2"),
            make_step(3, "click", {"element_id": "e1"}, dom_hash="h3"),
            make_step(4, "click", {"element_id": "e2"}, dom_hash="h4"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING
        ]

    def test_stuck_when_tail_hashes_frozen(self) -> None:
        # Alternating args (no verbatim repeats => no LOOPING), but the page
        # stopped changing from h2 onwards: 5 identical tail hashes >= 4.
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h2"),
            make_step(3, "click", {"element_id": "e1"}, dom_hash="h2"),
            make_step(4, "click", {"element_id": "e2"}, dom_hash="h2"),
            make_step(5, "click", {"element_id": "e1"}, dom_hash="h2"),
            make_step(6, "click", {"element_id": "e2"}, dom_hash="h2"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [FailureTag.STEP_BUDGET_EXCEEDED_STUCK]

    def test_timeout_gets_the_same_distinction(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h2"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_TIMEOUT)
        assert classify_failure(trace) == [
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING
        ]

    def test_boundary_exactly_at_stuck_threshold_is_stuck(self) -> None:
        # Boundary contract: a frozen tail of EXACTLY STUCK_TAIL_MIN_REPEATS
        # (4) identical hashes counts as stuck; one fewer does not.
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h1"),
            make_step(3, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(4, "click", {"element_id": "e2"}, dom_hash="h1"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [FailureTag.STEP_BUDGET_EXCEEDED_STUCK]

    def test_boundary_just_below_stuck_threshold_is_progressing(self) -> None:
        # Same shape but the tail repeats only 3 times (< 4): the agent was
        # still moving at the end => progressing. This documents that the
        # threshold is "tail repeats >= 4", not something vaguer.
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h0"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h1"),
            make_step(3, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(4, "click", {"element_id": "e2"}, dom_hash="h1"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING
        ]

    def test_frozen_first_half_but_lively_end_is_progressing(self) -> None:
        # Reviewer boundary case: hashes frozen on the FIRST half of the run
        # but changing again at the end. The stuck/progressing distinction is
        # deliberately decided by the TAIL (the state when the budget ran
        # out), so early stagnation followed by recovery counts as
        # progressing — documented in _budget_tags.
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h1"),
            make_step(3, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(4, "click", {"element_id": "e2"}, dom_hash="h1"),
            make_step(5, "click", {"element_id": "e1"}, dom_hash="h2"),
            make_step(6, "click", {"element_id": "e2"}, dom_hash="h3"),
            make_step(7, "click", {"element_id": "e1"}, dom_hash="h4"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == [
            FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING
        ]

    def test_too_few_hashes_no_budget_tag(self) -> None:
        # One hash is inconclusive evidence; classifier must stay silent
        # instead of guessing.
        steps = [make_step(1, "click", {"element_id": "e1"}, dom_hash="h1")]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert classify_failure(trace) == []


class TestToolErrorMishandled:
    def test_identical_repeat_after_failure(self) -> None:
        args = {"element_id": "e1", "value": "x"}
        steps = [
            # Fails, then the model resubmits the SAME call once: below the
            # loop threshold (2 repeats < 3) but clearly mishandled.
            make_step(1, "select_option", args, tool_success=False),
            make_step(2, "select_option", args, tool_success=False),
            make_step(3, "submit_form", {}),
            make_step(4, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.TOOL_ERROR_MISHANDLED]

    def test_adaptation_after_failure_is_not_mishandled(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e1"}, tool_success=False),
            # Different action afterwards: the model read the error and adapted.
            make_step(2, "type_text", {"element_id": "e1", "text": "a"}),
            make_step(3, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == []


class TestPrematureDone:
    def test_early_success_claim_suspicion(self) -> None:
        steps = [
            make_step(1, "navigate", {"url": "/form.html"}),
            make_step(2, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.PREMATURE_DONE]

    def test_late_done_is_not_premature(self) -> None:
        steps = [
            make_step(1, "navigate", {"url": "/form.html"}),
            make_step(2, "click", {"element_id": "e1"}),
            make_step(3, "submit_form", {}),
            make_step(4, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == []


class TestGracefulGiveUp:
    def test_give_up_on_verifier_failure(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e1"}),
            make_step(2, "done", {"success": False}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert classify_failure(trace) == [FailureTag.GRACEFUL_GIVE_UP]

    def test_honest_pessimist_success_outcome_special_case(self) -> None:
        # Rare but real: the agent declared failure, yet the verifier scored
        # the final page state as SUCCESS. The tag must survive on a SUCCESS
        # outcome — this combination is worth tracking separately.
        steps = [make_step(1, "done", {"success": False})]
        trace = make_trace(steps, RunOutcome.SUCCESS, verifier_success=True)
        assert classify_failure(trace) == [FailureTag.GRACEFUL_GIVE_UP]

class TestMultipleTagsAndCleanRuns:
    def test_multiple_tags_simultaneously(self) -> None:
        # Engineered to earn THREE tags at once:
        #   - HALLUCINATED_ELEMENT: every click targets e9, never listed;
        #   - TOOL_ERROR_MISHANDLED: each failed click is verbatim-repeated;
        #   - LOOPING: three consecutive identical clicks.
        # PREMATURE_DONE stays silent (done lands at step 4 > 2).
        bad = {"element_id": "e9"}
        steps = [
            make_step(1, "click", bad, tool_success=False),
            make_step(2, "click", bad, tool_success=False),
            make_step(3, "click", bad, tool_success=False),
            make_step(4, "done", {"success": True}),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_VERIFIER, verifier_success=False)
        assert set(classify_failure(trace)) == {
            FailureTag.HALLUCINATED_ELEMENT,
            FailureTag.LOOPING,
            FailureTag.TOOL_ERROR_MISHANDLED,
        }

    def test_clean_successful_run_gets_no_tags(self) -> None:
        # A normal, healthy run: real actions, ids from the latest snapshot,
        # honest done. The classifier must stay completely silent.
        steps = [
            make_step(1, "navigate", {"url": "/form.html"}, dom_hash="h1"),
            make_step(2, "type_text", {"element_id": "e1", "text": "Ada"}, dom_hash="h2"),
            make_step(3, "submit_form", {}, dom_hash="h3"),
            make_step(4, "done", {"success": True, "confidence": 0.95}),
        ]
        trace = make_trace(steps, RunOutcome.SUCCESS, verifier_success=True)
        assert classify_failure(trace) == []

    def test_crash_trace_without_steps_yields_no_tags(self) -> None:
        # Nothing inferable from an empty step sequence: no guessing.
        trace = make_trace([], RunOutcome.FAILURE_CRASH)
        assert classify_failure(trace) == []

    def test_classifier_is_pure_and_deterministic(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e9"}, tool_success=False),
            make_step(2, "click", {"element_id": "e9"}, tool_success=False),
            make_step(3, "click", {"element_id": "e9"}, tool_success=False),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        first = classify_failure(trace)
        second = classify_failure(trace)
        assert first == second
        assert trace.steps[0].tool_result is not None  # nothing mutated

    def test_thresholds_are_tunable(self) -> None:
        args = {"element_id": "e1", "text": "hello"}
        steps = [
            make_step(1, "type_text", args, dom_hash="h1"),
            make_step(2, "type_text", args, dom_hash="h2"),
        ]
        trace = make_trace(steps, RunOutcome.FAILURE_MAX_STEPS)
        assert FailureTag.LOOPING not in classify_failure(trace)
        # Tightening the threshold to 2 makes the same trace a loop.
        tags = classify_failure(trace, looping_min_repeats=2)
        assert FailureTag.LOOPING in tags


class TestBackwardCompatibility:
    def test_phase3_json_without_task_category_still_loads(self) -> None:
        # A trace serialized by the Phase 3 runner has no task_category key.
        # The Phase 4 extension must not break its deserialization.
        legacy_json = (
            "{"
            '"run_id":"run-legacy",'
            '"task_id":"task-old",'
            '"provider_name":"provider-a",'
            '"started_at":"2026-01-01T00:00:00Z",'
            '"finished_at":"2026-01-01T00:01:00Z",'
            f'"outcome":"{RunOutcome.SUCCESS.value}",'
            '"steps":[],'
            '"wall_clock_seconds":60.0'
            "}"
        )
        from agentalyze.runner.trace import RunTrace

        trace = RunTrace.model_validate_json(legacy_json)
        assert trace.task_category is None
        assert classify_failure(trace) == []


