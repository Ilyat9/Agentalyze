"""Failure taxonomy: explainable heuristics classifying HOW an agent run failed.

``RunOutcome`` (Phase 3) says *that* a run failed; this module says *how*, by
inspecting the recorded step sequence of a single ``RunTrace``. Every tag is
produced by a concrete, documented heuristic over ``StepEvent`` data — the
goal is reproducible diagnosis, not impressionistic labeling.

Guarantees:

* :func:`classify_failure` is **pure**: it reads only the ``trace`` object it
  is handed (no files, no clock, no randomness), so tests can feed it
  hand-built traces without ever starting a browser or a model;
* a single run may earn **several** tags at once (e.g. ``TOOL_ERROR_MISHANDLED``
  together with ``LOOPING``, when the agent keeps walking into the same tool
  error verbatim);
* tags are assigned only to ``FAILURE_*`` outcomes, with one documented
  exception: ``GRACEFUL_GIVE_UP`` is also meaningful when the verifier scored
  the run SUCCESS but the agent itself had declared failure — a rare but real
  combination (the "honest pessimist") tracked deliberately, since an honest
  "I couldn't" must be evaluated differently from a hang or a budget blowout.
"""

from __future__ import annotations

import json
import re
from enum import Enum

from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent


class FailureTag(str, Enum):
    """Fine-grained failure modes; one run can carry several of these."""

    WRONG_TOOL_CHOICE = "wrong_tool_choice"
    HALLUCINATED_ELEMENT = "hallucinated_element"
    LOOPING = "looping"
    STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING = "step_budget_exceeded_while_progressing"
    STEP_BUDGET_EXCEEDED_STUCK = "step_budget_exceeded_stuck"
    TOOL_ERROR_MISHANDLED = "tool_error_mishandled"
    PREMATURE_DONE = "premature_done"
    GRACEFUL_GIVE_UP = "graceful_give_up"
    #: Code-agent runner only (agentalyze.runner.code_agent): the model's
    #: generated Python code raised while executing inside smolagents'
    #: executor — the code-generation-path equivalent of TOOL_ERROR_MISHANDLED
    #: for a run that never reached a coherent action at all.
    CODE_EXECUTION_ERROR = "code_execution_error"
    #: Code-agent runner only: smolagents could not extract a code block from
    #: the model's response at all (smolagents.utils.AgentParsingError) —
    #: distinct from CODE_EXECUTION_ERROR, where a code block WAS extracted
    #: and ran, but raised.
    UNPARSEABLE_CODE_RESPONSE = "unparseable_code_response"


# ---------------------------------------------------------------------------
# Tunables — each threshold documents WHY it has the value it has.
# ---------------------------------------------------------------------------

#: Minimum number of *consecutive, effectively identical* tool invocations
#: (same tool name, same normalized arguments) that counts as LOOPING.
#:
#: Why 3: at temperature 0 a single verbatim retry after a failed action can
#: be a legitimate response to a transient glitch, and even a second one may
#: be deliberate persistence ("the page may have needed a moment"). Three or
#: more byte-equivalent calls in a row virtually never surface new
#: information and reliably indicate a loop. Below 3, false positives appear
#: on healthy traces (legitimate retry pairs); above 3, short loops escape.
LOOP_MIN_CONSECUTIVE_REPEATS = 3

#: How many consecutive actions must leave the DOM snapshot hash untouched
#: before a budget-exhausted run counts as STEP_BUDGET_EXCEEDED_STUCK.
#:
#: Why 4: one unchanged hash is normal for read-only steps (extract_text /
#: wait_for legitimately do not move the page), two can be a brief pause,
#: but four consecutive actions with zero page movement mean the agent is
#: pushing on a locked door while burning most of its remaining budget.
STUCK_TAIL_MIN_REPEATS = 4

#: A ``FAILURE_VERIFIER`` run where the model called ``done(success=true)``
#: at or before this step number earns a PREMATURE_DONE tag.
#:
#: Why 2: every task in the suite requires at least observe -> act ->
#: confirm, so a success claim within the first two steps leaves no room for
#: the actual work; paired with a verifier failure it very likely means the
#: model declared victory before doing anything. This tag is explicitly a
#: **suspicion**, not proof: a genuinely trivial task CAN finish legally in
#: two steps — consumers must present it as "suspected premature done".
PREMATURE_DONE_MAX_STEP = 2

# ---------------------------------------------------------------------------
# Static knowledge about the runner's tool surface.
#
# Duplicated from ``agentalyze.runner.tools`` ON PURPOSE: that module imports
# Playwright at module scope, and the analysis layer must stay usable with
# only ``pip install -e ".[dev]"`` (no browser extra). Keep these in sync
# with TOOL_SPECS / DONE_TOOL_NAME there.
# ---------------------------------------------------------------------------

DONE_TOOL_NAME = "done"

#: Every tool name the Phase 3 runner understands.
KNOWN_TOOL_NAMES = frozenset({
    "navigate",
    "click",
    "type_text",
    "select_option",
    "submit_form",
    "extract_text",
    "wait_for",
    DONE_TOOL_NAME,
})

#: Tools whose success can move the task forward by mutating page state
#: (or relocating it). Read-only introspection is excluded deliberately.
STATE_CHANGING_TOOLS = frozenset({
    "navigate",
    "click",
    "type_text",
    "select_option",
    "submit_form",
})

_ELEMENT_ID_RE = re.compile(r"^e\d+$")
_OBSERVED_ID_RE = re.compile(r"\[(e\d+)\]")


# ---------------------------------------------------------------------------
# Small pure helpers (all module-private).
# ---------------------------------------------------------------------------

def _normalize_value(value: object) -> object:
    """Normalize one tool-call argument for near-equality comparison.

    Strings are whitespace-stripped and case-folded: models repeating an
    action often wiggle whitespace or letter case inside free-text fields,
    and such cosmetic drift should not hide a loop. Other values (numbers,
    booleans, nested containers) are compared structurally as-is.
    """
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _signature(tool_name: str, arguments: dict[str, object]) -> str:
    """Canonical string identifying a tool invocation (name + arguments)."""
    normalized = {key: _normalize_value(value) for key, value in arguments.items()}
    return json.dumps([tool_name, normalized], sort_keys=True, ensure_ascii=False)


def _acting_steps(steps: list[StepEvent]) -> list[StepEvent]:
    """Steps where the model actually invoked a tool (including ``done``)."""
    return [step for step in steps if step.tool_call is not None]


def _declared_give_up(acting: list[StepEvent]) -> bool:
    """True when the model called ``done(success=false)`` at least once."""
    return any(
        step.tool_call is not None
        and step.tool_call.name == DONE_TOOL_NAME
        and not step.tool_call.arguments.get("success")
        for step in acting
    )


def _first_successful_done_step(acting: list[StepEvent]) -> int | None:
    """Step number of the first ``done(success=true)``, if any."""
    for step in acting:
        call = step.tool_call
        if call is not None and call.name == DONE_TOOL_NAME and call.arguments.get("success"):
            return step.step_number
    return None


def _last_observation_text(step: StepEvent) -> str:
    """The observation the model saw immediately before acting on this step.

    The runner appends exactly one fresh observation (the user message on
    step 1, a tool message afterwards) as the LAST user/tool message of each
    request, so scanning backwards is faithful to what the model saw.
    """
    for message in reversed(step.llm_request_messages):
        if message.role in ("user", "tool"):
            return message.content
    return ""


def _referenced_unseen_element(acting: list[StepEvent]) -> bool:
    """Did the model act on an element id absent from its latest observation?

    Element ids are re-assigned every step (see ``runner.observation``), so
    citing an id that the latest ELEMENTS list does not contain is a genuine
    hallucination signal. If the latest observation contains no ids at all
    (degenerate/empty page), the step is skipped: there is nothing to check
    against and guessing would produce false positives.
    """
    for step in acting:
        call = step.tool_call
        if call is None or call.name == DONE_TOOL_NAME:
            continue
        referenced = {
            value.strip()
            for value in call.arguments.values()
            if isinstance(value, str) and _ELEMENT_ID_RE.match(value.strip())
        }
        if not referenced:
            continue
        observed = set(_OBSERVED_ID_RE.findall(_last_observation_text(step)))
        if observed and referenced - observed:
            return True
    return False


def _longest_identical_run(signatures: list[str]) -> int:
    """Longest run of consecutive identical call signatures."""
    longest = 0
    current = 0
    previous: str | None = None
    for signature in signatures:
        current = current + 1 if signature == previous else 1
        previous = signature
        longest = max(longest, current)
    return longest


def _repeated_failed_action(acting: list[StepEvent], signatures: list[str]) -> bool:
    """Did the model verbatim-repeat an action right after it returned failure?

    This is the precise observable behind TOOL_ERROR_MISHANDLED: the tool
    said ``success=false`` and the very next step resubmits the identical
    call instead of adapting.
    """
    for index in range(1, len(acting)):
        previous_result = acting[index - 1].tool_result
        if (
            previous_result is not None
            and not previous_result.success
            and signatures[index] == signatures[index - 1]
        ):
            return True
    return False


def _budget_tags(acting: list[StepEvent], stuck_tail_min_repeats: int) -> set[FailureTag]:
    """Classify a budget-exhausted run as stuck vs still-progressing.

    Evidence: the tail of the ``dom_snapshot_hash`` sequence. A long run of
    identical hashes at the end means the page stopped responding to the
    agent's actions long before its budget ran out (STUCK); otherwise the
    agent was still changing page state when the budget hit zero
    (WHILE_PROGRESSING — a different failure mode: it needed more budget,
    not better recovery).

    With fewer than two recorded hashes the evidence is inconclusive (e.g.
    the run died on step 1, or hashing was unavailable), and NEITHER tag is
    emitted rather than guessing.
    """
    hashes = [
        step.tool_result.dom_snapshot_hash
        for step in acting
        if step.tool_result is not None and step.tool_result.dom_snapshot_hash
    ]
    if len(hashes) < 2:
        return set()
    tail = hashes[-1]
    repeats = 0
    for digest in reversed(hashes):
        if digest != tail:
            break
        repeats += 1
    if repeats >= stuck_tail_min_repeats:
        return {FailureTag.STEP_BUDGET_EXCEEDED_STUCK}
    return {FailureTag.STEP_BUDGET_EXCEEDED_WHILE_PROGRESSING}


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------

def classify_failure(
    trace: RunTrace,
    *,
    looping_min_repeats: int = LOOP_MIN_CONSECUTIVE_REPEATS,
    stuck_tail_min_repeats: int = STUCK_TAIL_MIN_REPEATS,
    premature_done_max_step: int = PREMATURE_DONE_MAX_STEP,
) -> list[FailureTag]:
    """Assign zero or more :class:`FailureTag` s to one run, deterministically.

    Pure: derives everything from the given ``trace`` object alone. Keyword
    thresholds exist so experiments can retune sensitivity without editing
    constants; defaults are the documented module constants.

    Semantics worth calling out:

    * ``PREMATURE_DONE`` is a *suspicion* heuristic (see the constant's
      rationale) — downstream reporting must phrase it as suspected, never
      as established fact;
    * ``GRACEFUL_GIVE_UP`` fires whenever the agent called
      ``done(success=false)``. On a ``SUCCESS``-outcome run that combination
      means the agent wrongly believed it had failed while the verifier
      disagreed — rare, but real, and deliberately reported instead of being
      swallowed by the "no failure tags on success" rule.
    """
    acting = _acting_steps(trace.steps)
    signatures = [
        _signature(call.name, call.arguments)
        for step in acting
        if (call := step.tool_call) is not None
    ]

    gave_up = _declared_give_up(acting)

    if trace.outcome is RunOutcome.SUCCESS:
        # Failure tags are meaningless on a successful run — except the
        # honest-pessimist case described in the docstring above.
        return [FailureTag.GRACEFUL_GIVE_UP] if gave_up else []

    tags: set[FailureTag] = set()

    if gave_up:
        tags.add(FailureTag.GRACEFUL_GIVE_UP)

    # WRONG_TOOL_CHOICE, two concrete observables:
    #   1. the model invoked a tool name that does not exist in the runner's
    #      registry (an invented/misnamed tool cannot advance the task);
    #   2. on FAILURE_VERIFIER the model reached a "done" verdict without EVER
    #      using a state-changing tool — in this task suite that means it
    #      picked reading/stalling tools where manipulation was required.
    # Rule 2 is restricted to verifier failures on purpose: a budget-exhausted
    # read-only run may be a legitimate waiting strategy that ran out of time,
    # which is a different diagnosis.
    unknown_tools = {
        call.name
        for step in acting
        if (call := step.tool_call) is not None
    } - KNOWN_TOOL_NAMES
    acted_without_mutation = bool(acting) and not any(
        (call := step.tool_call) is not None and call.name in STATE_CHANGING_TOOLS
        for step in acting
    )
    if unknown_tools or (
        acted_without_mutation and trace.outcome is RunOutcome.FAILURE_VERIFIER
    ):
        tags.add(FailureTag.WRONG_TOOL_CHOICE)

    if _referenced_unseen_element(acting):
        tags.add(FailureTag.HALLUCINATED_ELEMENT)

    # The max(2, ...) clamp keeps the API sane if someone passes a threshold
    # below 2: a single action can never be a "repeat" by definition, so the
    # effective minimum is always 2 consecutive identical calls.
    if _longest_identical_run(signatures) >= max(2, looping_min_repeats):
        tags.add(FailureTag.LOOPING)

    if _repeated_failed_action(acting, signatures):
        tags.add(FailureTag.TOOL_ERROR_MISHANDLED)

    # Both budget outcomes get the stuck/progressing distinction: a wall-clock
    # timeout mid-work carries the same diagnostic information as step-budget
    # exhaustion, and conflating "stuck" with "ran out of rope" would hide it.
    if trace.outcome in (RunOutcome.FAILURE_MAX_STEPS, RunOutcome.FAILURE_TIMEOUT):
        tags |= _budget_tags(acting, stuck_tail_min_repeats)

    # Code-agent-only tags: derived from the ``tool_error`` text the code-agent
    # runner (agentalyze.runner.code_agent.loop) writes for a step whose
    # smolagents ActionStep carried an AgentError. Checked across ALL steps,
    # not just ``acting`` ones, since a parsing/execution error step may have
    # no tool_call at all (the model's code never resolved to a real action).
    for step in trace.steps:
        if not step.tool_error:
            continue
        if step.tool_error.startswith("AgentParsingError"):
            tags.add(FailureTag.UNPARSEABLE_CODE_RESPONSE)
        elif step.tool_error.startswith(("AgentExecutionError", "AgentToolCallError",
                                          "AgentToolExecutionError")):
            tags.add(FailureTag.CODE_EXECUTION_ERROR)

    done_step = _first_successful_done_step(acting)
    if (
        trace.outcome is RunOutcome.FAILURE_VERIFIER
        and done_step is not None
        and done_step <= premature_done_max_step
    ):
        tags.add(FailureTag.PREMATURE_DONE)

    # Stable, declaration-ordered output; deduplicated via the set above.
    return [tag for tag in FailureTag if tag in tags]


# The ``Task.expected_failure_modes`` field (tasks/models.py) is typed with this
# enum via a forward reference, because a module-level import would close an
# import cycle (tasks.models -> analysis -> runner.trace -> tasks.models). Now
# that FailureTag fully exists, rebuild the model so the forward reference
# resolves — after this line every Task construction validates the field as
# list[FailureTag] regardless of which module was imported first.
from agentalyze.tasks.models import Task as _TaskWithFailureModes

_TaskWithFailureModes.model_rebuild()


