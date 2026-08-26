"""Structural diff of two RunTraces of the SAME (task, provider) pair.

Purpose: when ``regression-check`` flags a regression, this module answers
the next natural question — WHERE exactly did the agent's behaviour change?
It compares step sequences line by line and pinpoints the FIRST divergent
step number.

Deliberate scope limits (honesty about what is implemented):

* Structural comparison ONLY: per aligned step it reports whether the tool
  call name changed, whether the tool's success/fail result changed, and
  whether the post-action page state (``dom_snapshot_hash``) differs. It does
  NOT attempt to interpret WHY anything changed — that would be an
  unfalsifiable heuristic, not measurement. The human reads the two traces
  themselves, starting from the reported first divergence.
* Not a raw JSON ``diff``: full LLM message payloads are ignored entirely;
  steps are aligned by ``step_number`` where meaningful, and length mismatch
  is reported explicitly ("steps only in baseline/new") instead of being
  silently truncated to the shorter sequence.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentalyze.runner.trace import RunTrace, StepEvent


class StepComparison(BaseModel):
    """One aligned (by step_number) pair of steps."""

    step_number: int
    baseline_tool_name: str | None = None
    new_tool_name: str | None = None
    #: True when the invoked tool differs between the two runs.
    tool_changed: bool = False
    #: True when the tool's success/fail outcome differs (or one side has no
    #: tool result while the other does).
    tool_result_changed: bool = False
    #: True when the post-action DOM state differs; None when either side has
    #: no hash (e.g. no action ran), so nothing can be asserted.
    dom_state_diverged: bool | None = None


class TraceDiff(BaseModel):
    """The structural comparison of two traces of one (task, provider)."""

    task_id: str
    provider_name: str
    baseline_run_id: str
    new_run_id: str
    baseline_step_count: int
    new_step_count: int
    comparisons: list[StepComparison] = Field(default_factory=list)
    #: Step numbers present only on one side (length mismatch tails).
    steps_only_in_baseline: list[int] = Field(default_factory=list)
    steps_only_in_new: list[int] = Field(default_factory=list)
    #: Lowest step number where ANY signal diverged; None = identical sequences.
    first_divergent_step: int | None = None

# --- comparison + rendering appended below ---

def _tool_name(step_event: StepEvent) -> str | None:
    return step_event.tool_call.name if step_event.tool_call is not None else None


def compare_traces(baseline: RunTrace, new: RunTrace) -> TraceDiff:
    """Compare two ALREADY LOADED traces structurally. Pure function, no I/O."""
    if baseline.task_id != new.task_id or baseline.provider_name != new.provider_name:
        msg = (
            "--diff-trace compares traces of the SAME (task, provider) pair: "
            f"baseline is {baseline.task_id}/{baseline.provider_name}, "
            f"new is {new.task_id}/{new.provider_name}."
        )
        raise ValueError(msg)

    base_by_number = {step.step_number: step for step in baseline.steps}
    new_by_number = {step.step_number: step for step in new.steps}

    comparisons: list[StepComparison] = []
    first_divergent: int | None = None

    for number in sorted(base_by_number.keys() & new_by_number.keys()):
        base_step = base_by_number[number]
        new_step = new_by_number[number]

        base_tool = _tool_name(base_step)
        new_tool = _tool_name(new_step)
        tool_changed = base_tool != new_tool

        base_ok = (
            base_step.tool_result.success if base_step.tool_result is not None else None
        )
        new_ok = (
            new_step.tool_result.success if new_step.tool_result is not None else None
        )
        tool_result_changed = base_ok != new_ok

        base_hash = (
            base_step.tool_result.dom_snapshot_hash
            if base_step.tool_result is not None
            else None
        )
        new_hash = (
            new_step.tool_result.dom_snapshot_hash
            if new_step.tool_result is not None
            else None
        )
        dom_diverged: bool | None = (
            base_hash != new_hash if base_hash is not None and new_hash is not None
            else None
        )

        diverged_here = tool_changed or tool_result_changed or dom_diverged
        if diverged_here and first_divergent is None:
            first_divergent = number

        comparisons.append(
            StepComparison(
                step_number=number,
                baseline_tool_name=base_tool,
                new_tool_name=new_tool,
                tool_changed=tool_changed,
                tool_result_changed=tool_result_changed,
                dom_state_diverged=dom_diverged,
            )
        )

    return TraceDiff(
        task_id=baseline.task_id,
        provider_name=baseline.provider_name,
        baseline_run_id=baseline.run_id,
        new_run_id=new.run_id,
        baseline_step_count=len(baseline.steps),
        new_step_count=len(new.steps),
        comparisons=comparisons,
        steps_only_in_baseline=sorted(set(base_by_number) - set(new_by_number)),
        steps_only_in_new=sorted(set(new_by_number) - set(base_by_number)),
        first_divergent_step=first_divergent,
    )


def render_trace_diff(diff: TraceDiff) -> str:
    """Human-readable console rendering (the machine-readable artifacts remain
    the two trace.json files themselves)."""
    lines: list[str] = [
        f"Trace diff: task={diff.task_id}  provider={diff.provider_name}",
        f"  baseline: {diff.baseline_run_id} ({diff.baseline_step_count} step(s))",
        f"  new:      {diff.new_run_id} ({diff.new_step_count} step(s))",
        "",
    ]

    def _fmt(name: str | None) -> str:
        return name if name is not None else "<no tool>"

    for cmp_item in diff.comparisons:
        marks: list[str] = []
        if cmp_item.tool_changed:
            marks.append("TOOL CHANGED")
        if cmp_item.tool_result_changed:
            marks.append("RESULT CHANGED")
        if cmp_item.dom_state_diverged:
            marks.append("DOM STATE DIVERGED")
        suffix = f"  [{' | '.join(marks)}]" if marks else ""
        lines.append(
            f"  step {cmp_item.step_number}: "
            f"{_fmt(cmp_item.baseline_tool_name)} -> {_fmt(cmp_item.new_tool_name)}"
            f"{suffix}"
        )

    if diff.steps_only_in_baseline:
        ids = ", ".join(str(n) for n in diff.steps_only_in_baseline)
        lines.append(f"\n  Steps only in BASELINE (new run ended earlier): {ids}")
    if diff.steps_only_in_new:
        ids = ", ".join(str(n) for n in diff.steps_only_in_new)
        lines.append(f"\n  Steps only in NEW (new run took extra steps): {ids}")

    lines.append("")
    if diff.first_divergent_step is not None:
        lines.append(
            f"FIRST DIVERGENCE at step {diff.first_divergent_step}. "
            "(This report states WHERE the sequences differ; interpreting WHY "
            "is up to you — see the two trace.json files.)"
        )
    else:
        lines.append("No structural divergence found: the step sequences match.")
    return "\n".join(lines)
