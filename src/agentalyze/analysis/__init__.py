"""Analysis layer (Phase 4): structured metrics derived from RunTrace objects.

This package *computes* — it never presents (that is Phase 5) and never runs
anything itself (no browser, no model). Every public function consumes
in-memory ``RunTrace`` objects produced by ``agentalyze.runner`` and returns
plain Pydantic structures, which keeps the whole layer pure, fast and trivially
testable with hand-built traces.

Modules:

* ``failure_taxonomy`` — fine-grained, explainable failure tags per run;
* ``metrics``          — aggregated suite metrics with per-category breakdown;
* ``calibration``      — claimed-confidence vs verified-outcome calibration;
* ``pricing``/``cost`` — editable token-price table and USD conversion.
"""

from __future__ import annotations

from agentalyze.analysis.calibration import (
    CalibrationReport,
    compute_calibration_report,
)
from agentalyze.analysis.cost import compute_run_cost, summarize_costs
from agentalyze.analysis.failure_taxonomy import FailureTag, classify_failure
from agentalyze.analysis.metrics import CategoryMetrics, TaskSuiteMetrics, compute_metrics
from agentalyze.analysis.pricing import PricingConfig, load_pricing

__all__ = [
    "CalibrationReport",
    "CategoryMetrics",
    "FailureTag",
    "PricingConfig",
    "TaskSuiteMetrics",
    "classify_failure",
    "compute_calibration_report",
    "compute_metrics",
    "compute_run_cost",
    "load_pricing",
    "summarize_costs",
]
