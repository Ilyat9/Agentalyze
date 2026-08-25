"""Token usage -> USD conversion: exact arithmetic, never an eyeball estimate.

The semantic split this module exists to protect:

* ``None``  = "cost unknown" (the provider has no price-table entry);
* ``0.0``   = "known to be free" (e.g. local Ollama with ``free: true``).

These are different facts. Collapsing them would either overstate knowledge
(reporting $0 for an unpriced commercial API) or understate it (hiding real
savings from local models).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from agentalyze.analysis.pricing import PricingConfig
from agentalyze.runner.trace import RunTrace


def compute_run_cost(trace: RunTrace, pricing: PricingConfig) -> float | None:
    """USD cost of one run from its recorded token totals.

    Returns ``None`` when the provider has no price-table entry ("unknown"),
    and ``0.0`` for a known-free provider. Formula:
    ``prompt_tokens / 1000 * prompt_price + completion_tokens / 1000 * completion_price``.
    """
    entry = pricing.price_for(trace.provider_name)
    if entry is None:
        return None
    if entry.free:
        return 0.0
    assert entry.prompt_price_per_1k_usd is not None  # guaranteed by ModelPrice validation
    assert entry.completion_price_per_1k_usd is not None
    return (
        trace.total_prompt_tokens / 1000 * entry.prompt_price_per_1k_usd
        + trace.total_completion_tokens / 1000 * entry.completion_price_per_1k_usd
    )


def compute_costs(traces: list[RunTrace], pricing: PricingConfig) -> list[float | None]:
    """Per-run costs, order-preserving; entries may be ``None`` (unknown)."""
    return [compute_run_cost(trace, pricing) for trace in traces]


class CostTotals(NamedTuple):
    """Suite-level cost aggregates with explicit unknown-cost semantics."""

    #: None when ANY run's cost was unknown; otherwise the sum (0.0 means
    #: every run was genuinely free — not "we couldn't tell").
    total_cost_usd: float | None
    avg_cost_usd_per_task: float | None


def summarize_costs(costs: Sequence[float | None]) -> CostTotals:
    """Aggregate per-run costs; one unknown poisons the whole aggregate."""
    if any(cost is None for cost in costs):
        return CostTotals(total_cost_usd=None, avg_cost_usd_per_task=None)
    known = [cost for cost in costs if cost is not None]
    if not known:  # defensive: callers pass >= 1 run; keep it total anyway
        return CostTotals(total_cost_usd=0.0, avg_cost_usd_per_task=0.0)
    total = sum(known)
    return CostTotals(total_cost_usd=total, avg_cost_usd_per_task=total / len(known))
