"""Regenerate ``examples/sample_report.md`` WITHOUT any live model or browser.

The committed sample report exists so a repository visitor can see the main
feature (an honest, programmatically derived comparison report) without
configuring providers. This script builds fully synthetic ``RunTrace`` objects
for two imaginary providers over the real 18-task registry, feeds them through
the PRODUCTION pipeline (``compute_metrics`` + ``render_report``) and writes
the result to ``examples/sample_report.md``.

Nothing here is hand-written prose pretending to be tool output: every number,
table and sentence in the sample report comes from the same code path a real
suite run takes. Run it after changing the report format::

    python examples/generate_sample_report.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentalyze.analysis.metrics import compute_metrics
from agentalyze.orchestration.report import render_report
from agentalyze.orchestration.suite_runner import SuiteRunConfig, SuiteRunResult
from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, ToolResult
from agentalyze.tasks.models import TaskCategory, VerificationResult
from agentalyze.tasks.registry import TASKS_BY_ID

T0 = datetime(2026, 8, 20, 14, 0, 0, tzinfo=UTC)
CLOUD = "gpt-4o-mini-via-openrouter"
LOCAL = "llama31-8b-local"


def _step(
    index: int,
    tool_name: str,
    arguments: dict[str, object],
    *,
    latency: float,
    prompt_tokens: int,
    completion_tokens: int,
    dom_hash: str | None = "a" * 12,
    tool_ok: bool = True,
) -> StepEvent:
    """One synthetic Reason -> Act -> Observe round."""
    return StepEvent(
        step_number=index,
        timestamp=T0 + timedelta(seconds=index * latency),
        llm_request_messages=[ChatMessage(role="user", content="[synthetic context]")],
        llm_response=CompletionResult(
            message=ChatMessage(role="assistant", content=f"[{tool_name}]"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_seconds=latency,
            finish_reason="tool_calls",
        ),
        tool_call=ToolCall(id=f"call_{index}", name=tool_name, arguments=arguments),
        tool_result=ToolResult(success=tool_ok, output="ok", dom_snapshot_hash=dom_hash),
    )


def _done(index: int, success: bool, confidence: float | None, latency: float) -> StepEvent:
    args: dict[str, object] = {"success": success}
    if confidence is not None:
        args["confidence"] = confidence
    return _step(
        index,
        "done",
        args,
        latency=latency,
        prompt_tokens=2400,
        completion_tokens=40,
        dom_hash=None,
    )


def make_trace(
    task_id: str,
    provider: str,
    outcome: RunOutcome,
    steps: list[StepEvent],
    wall_clock: float,
    total_cost: float | None,
    verifier_success: bool,
) -> RunTrace:
    task = TASKS_BY_ID[task_id]
    return RunTrace(
        run_id=f"sample-{provider.split('-')[0]}-{task_id}",
        task_id=task.id,
        task_category=TaskCategory(task.category),
        provider_name=provider,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=wall_clock),
        outcome=outcome,
        verifier_result=(
            VerificationResult(
                success=verifier_success,
                reason=(
                    "Success marker present."
                    if verifier_success
                    else "Expected final state was NOT reached."
                ),
            )
            if outcome in (RunOutcome.SUCCESS, RunOutcome.FAILURE_VERIFIER)
            else None
        ),
        steps=steps,
        total_prompt_tokens=sum(s.llm_response.prompt_tokens for s in steps),
        total_completion_tokens=sum(s.llm_response.completion_tokens for s in steps),
        total_cost_usd=total_cost,
        wall_clock_seconds=wall_clock,
    )



def happy(provider: str, n_actions: int, confidence: float) -> list[StepEvent]:
    """A clean successful run: navigate, a few actions, honest done()."""
    lat = 1.4 if provider == CLOUD else 5.5
    steps = [_step(1, "navigate", {"url": "/fixture.html"}, latency=lat, prompt_tokens=1900,
                   completion_tokens=60)]
    for i in range(n_actions):
        steps.append(_step(i + 2, "click", {"selector": "#target"}, latency=lat,
                           prompt_tokens=2300, completion_tokens=45))
    steps.append(_done(len(steps) + 1, True, confidence, lat))
    return steps


def loop_then_fail(provider: str) -> list[StepEvent]:
    """LOOPING: three byte-identical clicks against an inert element, then give up."""
    lat = 1.6 if provider == CLOUD else 6.0
    steps = [_step(1, "navigate", {"url": "/fixture.html"}, latency=lat, prompt_tokens=1900,
                   completion_tokens=60)]
    steps += [_step(2, "click", {"selector": "#decoy-submit"}, latency=lat,
                    prompt_tokens=2500, completion_tokens=40, tool_ok=False)
              for _ in range(3)]
    steps.append(_done(5, False, 0.9, lat))
    return steps


def premature(provider: str, claimed_confidence: float) -> list[StepEvent]:
    """PREMATURE_DONE: declares success on the very first observation.

    The taxonomy heuristic only flags ``done`` calls made at step <= 2
    (``premature_done_max_step``), so the claim happens immediately after
    navigation — before any state-changing action could plausibly finish.
    """
    lat = 1.3 if provider == CLOUD else 5.0
    return [
        _step(1, "navigate", {"url": "/fixture.html"}, latency=lat, prompt_tokens=1900,
              completion_tokens=60),
        _done(2, True, claimed_confidence, lat),
    ]


def stuck_no_done(provider: str, n: int) -> list[StepEvent]:
    """STEP_BUDGET_EXCEEDED_STUCK: repeated clicks, page state never changes."""
    lat = 1.7 if provider == CLOUD else 6.5
    steps = [_step(1, "navigate", {"url": "/fixture.html"}, latency=lat, prompt_tokens=1900,
                   completion_tokens=60)]
    steps += [_step(i + 2, "click", {"selector": "#disabled-submit"}, latency=lat,
                    prompt_tokens=2600, completion_tokens=35, dom_hash="f" * 12)
              for i in range(n)]
    return steps


def wrong_tool(provider: str) -> list[StepEvent]:
    """WRONG_TOOL_CHOICE: invents a nonexistent tool, then claims success."""
    lat = 1.5 if provider == CLOUD else 5.8
    return [
        _step(1, "navigate", {"url": "/fixture.html"}, latency=lat, prompt_tokens=1900,
              completion_tokens=60),
        _step(2, "fill_form", {"fields": {"name": "x"}}, latency=lat, prompt_tokens=2400,
              completion_tokens=80, tool_ok=False),
        _done(3, True, 0.85, lat),
    ]


# --- Per-task scenarios -----------------------------------------------------
# (task_id -> (outcome, step-builder, wall clock)) for every NON-successful
# run; every task not listed here succeeds.

CLOUD_FAILURES: dict[str, tuple[RunOutcome, object, float]] = {
    # Cloud model: strong overall, but trips on the hardest traps.
    "multi-wizard-02": (RunOutcome.FAILURE_VERIFIER, lambda p: premature(p, 0.9), 9.8),
    "distractor-forms-03": (RunOutcome.FAILURE_VERIFIER, loop_then_fail, 14.2),
    "err-flaky-widget-03": (
        RunOutcome.FAILURE_MAX_STEPS,
        lambda p: stuck_no_done(p, 8),
        21.5,
    ),
}

LOCAL_FAILURES: dict[str, tuple[RunOutcome, object, float]] = {
    # Local 8B model: collapses exactly where the suite expects it to.
    "nav-tabs-secret-03": (RunOutcome.FAILURE_VERIFIER, lambda p: premature(p, 0.95), 16.4),
    "form-fill-validation-02": (
        RunOutcome.FAILURE_VERIFIER,
        lambda p: premature(p, 0.9),
        18.1,
    ),
    "form-fill-dependent-selects-03": (
        RunOutcome.FAILURE_MAX_STEPS,
        lambda p: stuck_no_done(p, 7),
        41.0,
    ),
    "extract-table-count-03": (RunOutcome.FAILURE_VERIFIER, wrong_tool, 22.7),
    "multi-shop-checkout-01": (RunOutcome.FAILURE_VERIFIER, loop_then_fail, 27.3),
    "err-dead-end-02": (RunOutcome.FAILURE_VERIFIER, wrong_tool, 25.0),
    "err-flaky-widget-03": (
        RunOutcome.FAILURE_MAX_STEPS,
        lambda p: stuck_no_done(p, 9),
        48.6,
    ),
    "distractor-links-02": (RunOutcome.FAILURE_VERIFIER, lambda p: premature(p, 0.95), 19.8),
    "distractor-forms-03": (RunOutcome.FAILURE_VERIFIER, loop_then_fail, 24.5),
}

#: Deterministic confidence labels for successful runs, cycling over the
#: [0.55, 0.95] range so the calibration section has enough populated bins.
SUCCESS_CONFIDENCES = [0.9, 0.8, 0.7, 0.95, 0.6, 0.85, 0.75, 0.55, 0.9]


def build_traces() -> list[RunTrace]:
    traces: list[RunTrace] = []
    for provider, failures, base_cost, n_actions in (
        (CLOUD, CLOUD_FAILURES, 0.0035, 4),
        (LOCAL, LOCAL_FAILURES, 0.0, 8),
    ):
        conf_index = 0
        for task_id in TASKS_BY_ID:
            if task_id in failures:
                outcome, builder, wall = failures[task_id]
                steps = builder(provider)
                traces.append(
                    make_trace(
                        task_id,
                        provider,
                        outcome,
                        steps,
                        wall,
                        # Local model: a genuine $0.00 (free), NOT an unknown
                        # price — that distinction is part of what the report
                        # teaches readers to see.
                        base_cost,
                        verifier_success=False,
                    )
                )
            else:
                confidence = SUCCESS_CONFIDENCES[conf_index % len(SUCCESS_CONFIDENCES)]
                conf_index += 1
                lat = 1.4 if provider == CLOUD else 5.5
                steps = happy(provider, n_actions, confidence)
                traces.append(
                    make_trace(
                        task_id,
                        provider,
                        RunOutcome.SUCCESS,
                        steps,
                        len(steps) * lat + 2.0,
                        base_cost,
                        verifier_success=True,
                    )
                )
    return traces


def main() -> None:
    traces = build_traces()
    metrics = {
        provider: compute_metrics([t for t in traces if t.provider_name == provider])
        for provider in (CLOUD, LOCAL)
    }
    result = SuiteRunResult(
        suite_run_id="20260820-140000-sample-fake-providers",
        started_at=T0,
        finished_at=T0 + timedelta(minutes=13, seconds=47),
        config=SuiteRunConfig(provider_names=[CLOUD, LOCAL]),
        traces=traces,
        metrics_by_provider=metrics,
    )
    report = render_report(result)
    header_note = (
        "> **Это заранее сгенерированный пример** — получен производственным\n"
        "> конвейером отчёта (`compute_metrics` + `render_report`) на\n"
        "> синтетических трейсах двух вымышленных провайдеров; реальная модель\n"
        "> не вызывалась. Воспроизведение: `python examples/generate_sample_report.py`.\n"
    )
    out_path = Path(__file__).resolve().parent / "sample_report.md"
    marker = "\n## Summary"
    head, found, tail = report.partition(marker)
    if not found:
        msg = "unexpected: rendered report has no Summary section"
        raise RuntimeError(msg)
    out_path.write_text(f"{head}\n{header_note}\n{marker}{tail}", encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()

