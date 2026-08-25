"""Run-trace data model: the durable record of a single agent run.

Everything Phases 4-6 build on (failure taxonomy, reports, regression diff)
is derived from :class:`RunTrace`, so it must be *self-sufficient*: a trace
serializes to JSON without information loss, including the full LLM message
context of every step. Storage layout on disk::

    {results_dir}/{run_id}/trace.json          <- serialized RunTrace
    {results_dir}/{run_id}/screenshots/step_N.png  <- post-action screenshots

Screenshots live in their own subdirectory rather than next to ``trace.json``
so the (potentially numerous) binary artifacts never bury the single file
machines consume.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.tasks.models import TaskCategory, VerificationResult


class RunOutcome(str, Enum):
    """Terminal classification of a run.

    The distinction that matters most for Phase 4 analysis is between
    ``FAILURE_VERIFIER`` (the agent *believed* it was done — via
    ``done(success=True)`` — but the verifier disagreed) and the cases where
    the agent never reached a conclusion (budgets exhausted) or the harness
    itself broke (provider/tool/crash).
    """

    SUCCESS = "success"
    FAILURE_VERIFIER = "failure_verifier"  # agent declared done, verifier disagreed (or agent gave up)
    FAILURE_MAX_STEPS = "failure_max_steps"  # step budget exhausted without `done`
    FAILURE_TIMEOUT = "failure_timeout"  # wall-clock budget exhausted
    FAILURE_PROVIDER_ERROR = "failure_provider_error"  # provider failed after retries (Phase 2)
    FAILURE_TOOL_ERROR = (
        "failure_tool_error"  # unhandled browser-tool exception; frequent occurrence = runner bug
    )
    FAILURE_CRASH = "failure_crash"  # unhandled runner exception, last resort, keeps full traceback


class ToolResult(BaseModel):
    """What actually happened in the world after a tool invocation.

    ``output`` is exactly what the model receives as its next-step
    observation. ``dom_snapshot_hash`` is a cheap fingerprint (sha256 of
    whitespace-normalized HTML) of the page state *after* the action: full DOM
    snapshots would bloat traces, but the hash already lets future phases
    detect the stuck-on-the-same-state failure pattern.
    """

    success: bool
    output: str
    dom_snapshot_hash: str | None = Field(
        default=None,
        description="sha256 of the normalized page HTML after the action, when known.",
    )


class StepEvent(BaseModel):
    """One Reason -> Act -> Observe round of the ReAct loop."""

    step_number: int
    timestamp: datetime
    # --- Reason: the full context the model saw and what it answered --------
    llm_request_messages: list[ChatMessage] = Field(
        description="Complete message list sent to the model on this step.",
    )
    llm_response: CompletionResult
    # --- Act -----------------------------------------------------------------
    tool_call: ToolCall | None = Field(
        default=None,
        description="None when the model produced no tool call on this step.",
    )
    # --- Observe -------------------------------------------------------------
    tool_result: ToolResult | None = None
    tool_error: str | None = Field(
        default=None,
        description="Concrete cause when the tool invocation itself failed.",
    )
    screenshot_path: str | None = Field(
        default=None,
        description="PNG of the page state AFTER this step's action (None when no action ran).",
    )


class RunTrace(BaseModel):
    """Full, self-sufficient record of one task run by one provider."""

    run_id: str = Field(description="UUID identifying this run; also the artifact directory name.")
    task_id: str
    # --- Phase 4 extension: analysis needs the task's category for the
    # --- per-category metric breakdown. Optional with a None default so
    # --- traces saved by Phase 3 deserialize unchanged; such old traces are
    # --- simply skipped in `by_category` aggregations.
    task_category: TaskCategory | None = Field(
        default=None,
        description=(
            "Category of the task this run belongs to (Phase 4+). None on "
            "traces written before this field existed; they still load fine."
        ),
    )
    provider_name: str
    started_at: datetime
    finished_at: datetime
    outcome: RunOutcome
    verifier_result: VerificationResult | None = Field(
        default=None,
        description=(
            "None when verification was never reached (budget exhausted, "
            "provider/tool failure, crash)."
        ),
    )
    steps: list[StepEvent] = Field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float | None = Field(
        default=None,
        description=(
            "None unless pricing is configured for the provider; the harness "
            "never invents a number."
        ),
    )
    wall_clock_seconds: float = Field(ge=0)
    error: str | None = Field(
        default=None,
        description=(
            "Full traceback for FAILURE_CRASH / provider error details; "
            "None on clean terminations."
        ),
    )

    @property
    def success(self) -> bool:
        return self.outcome is RunOutcome.SUCCESS


def run_dir(results_dir: Path, run_id: str) -> Path:
    """Artifact directory for one run."""
    return Path(results_dir) / run_id


def screenshots_dir(results_dir: Path, run_id: str) -> Path:
    """Screenshot subdirectory for one run."""
    return run_dir(results_dir, run_id) / "screenshots"


def save_trace(trace: RunTrace, results_dir: Path) -> Path:
    """Serialize ``trace`` to ``{results_dir}/{run_id}/trace.json`` and return the path."""
    directory = run_dir(results_dir, trace.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "trace.json"
    path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_trace(path: Path) -> RunTrace:
    """Read back a trace written by :func:`save_trace` without information loss."""
    return RunTrace.model_validate_json(Path(path).read_text(encoding="utf-8"))
