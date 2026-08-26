"""Pydantic models describing benchmark tasks and verification results.

Two clearly separated layers live in this subpackage:

* **Public task API** (``Task``, ``TaskCategory``) — everything an agent
  runner (Phase 3) is allowed to see. In particular ``fixture_url_path`` is a
  *relative* URL path: the absolute ``http://127.0.0.1:<port>`` prefix is only
  known at runtime when the fixture server binds a free port.
* **Reference data** (see ``agentalyze.tasks.reference``) — selectors and
  expected values used *only* to validate fixtures programmatically. It must
  never be handed to the agent as a hint; agents are supposed to find
  elements themselves, like on a real website.

Fixtures are served over plain HTTP (never ``file://``): real browser agents
(including the test subject) typically carry SSRF guards tuned for
``http(s)://`` URLs, and ``file://`` behaves differently for relative CSS/JS
paths and form submissions.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    # Imported lazily at runtime (see the model_rebuild note in
    # analysis/failure_taxonomy.py): a module-level import here would create
    # an import cycle tasks.models -> analysis -> runner.trace -> tasks.models.
    from agentalyze.analysis.failure_taxonomy import FailureTag


class TaskCategory(str, Enum):
    """Categories of agentic web tasks. Each category targets specific failure modes."""

    NAVIGATION = "navigation"
    FORM_FILL = "form_fill"
    EXTRACTION = "extraction"
    MULTI_STEP = "multi_step"
    ERROR_RECOVERY = "error_recovery"
    DISTRACTOR = "distractor"


class Task(BaseModel):
    """A single benchmark task.

    ``description`` is given to the agent verbatim; everything else is harness
    metadata. Success is decided exclusively by the verifier registered under
    ``verifier_id`` (see ``agentalyze.tasks.verifiers.VERIFIERS``), based on the
    final DOM state of the page — never by replaying or inspecting agent steps.
    """

    id: str = Field(
        description="Unique task id, kebab-case (e.g. 'form-fill-basic-01').",
    )
    category: TaskCategory
    title: str = Field(description="Short human-readable name.")
    description: str = Field(
        description="Instruction given to the agent word-for-word.",
    )
    fixture_path: str = Field(
        description=(
            "Path of the HTML fixture relative to the fixtures/ directory "
            "(e.g. 'form_fill/basic_01.html')."
        ),
    )
    fixture_url_path: str = Field(
        description=(
            "Absolute URL path under which the fixture server exposes the "
            "fixture (e.g. '/form_fill/basic_01.html'). The full "
            "http://127.0.0.1:<port> base URL is assembled at runtime."
        ),
    )
    verifier_id: str = Field(
        description="Key into agentalyze.tasks.verifiers.VERIFIERS deciding success.",
    )
    max_steps: int = Field(
        default=25,
        ge=1,
        description="ReAct step budget for this specific task.",
    )
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        description="Wall-clock budget for the whole task.",
    )
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[str] = Field(default_factory=list)
    #: Failure modes (FailureTag values) this task is EXPECTED to expose when an
    #: agent fails at it — the structured counterpart of the registry's free-form
    #: "Reveals: …" comment. Optional for backward compatibility with tasks added
    #: before the field existed; new tasks MUST fill it (see CONTRIBUTING.md).
    expected_failure_modes: list[FailureTag] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_is_kebab_case(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value):
            msg = f"Task id must be kebab-case, got {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("fixture_path")
    @classmethod
    def _fixture_path_is_relative(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            msg = f"fixture_path must be relative and stay inside fixtures/, got {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("fixture_url_path")
    @classmethod
    def _url_path_is_absolute(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value.split("/"):
            msg = f"fixture_url_path must be an absolute URL path, got {value!r}"
            raise ValueError(msg)
        return value


class VerificationResult(BaseModel):
    """Outcome of checking the final page state against expectations.

    ``reason`` must explain *why* success is True/False — this is critical for
    failure analysis in Phase 4. ``extracted_value`` carries what the page (or
    the agent via the page) actually returned for EXTRACTION-style checks.
    """

    success: bool
    reason: str
    extracted_value: str | None = None
