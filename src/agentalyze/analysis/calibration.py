"""Confidence calibration: does claimed confidence match verified reality?

The contract being measured: if a model says "90% confident" via
``done(..., confidence=0.9)``, then across all such declarations it should be
right (per the Phase 1 programmatic verifier) about 90% of the time.

Runs that never supplied a confidence value simply don't participate — that
is expected, not an error (neither all tasks nor all models must report it).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from agentalyze.runner.trace import RunTrace

#: Default number of equal-width calibration bins over [0, 1] (step 0.1).
DEFAULT_N_BINS = 10

#: Warn when fewer than this many bins contain data. Why 3: ECE over one or
#: two populated bins is essentially "accuracy in one confidence bucket" —
#: far too little coverage of the [0, 1] range to call the number reliable.
LOW_STATISTICS_MIN_NONEMPTY_BINS = 3

#: Tolerance added before flooring when assigning a confidence to a bin.
#: Why: ``conf * n_bins`` in floating point can land an exact-decimal value a
#: hair BELOW its mathematical bin edge (e.g. ``0.29 * 100`` evaluates to
#: ``28.999999999999996``, flooring into bin 28 instead of 29). A 1e-12
#: nudge repairs that while being far smaller than any meaningful gap between
#: distinct reported confidences, so legitimate just-below-edge values keep
#: their bin.
_BIN_EDGE_EPSILON = 1e-12


class CalibrationBin(BaseModel):
    """One equal-width slice of [0, 1] with the runs that landed in it."""

    index: int = Field(ge=0, description="Zero-based bin index; range [index/n, (index+1)/n).")
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)
    count: int = Field(default=0, ge=0)
    #: Mean claimed confidence of the runs in this bin (None when empty).
    avg_confidence: float | None = None
    #: Fraction of those runs the verifier scored as success (None when empty).
    success_rate: float | None = None


class CalibrationReport(BaseModel):
    """Bins + ECE + honesty about how much data backs the numbers."""

    n_bins: int = Field(ge=1)
    bins: list[CalibrationBin]
    #: How many runs actually participated (reported a usable confidence).
    runs_with_confidence: int = Field(ge=0)
    #: Expected Calibration Error:
    #:   ECE = sum over non-empty bins B of (|B| / N) * |avg_conf(B) - acc(B)|
    #: i.e. the size-weighted mean absolute gap between claimed confidence
    #: and observed success rate. Standard formulation (Naeini et al. 2015,
    #: Guo et al. 2017); None when no run supplied a confidence at all.
    ece: float | None = None
    #: Human-readable caveat when the data is too thin to trust the ECE.
    low_statistics_warning: str | None = None


def collect_confidence_pairs(traces: list[RunTrace]) -> list[tuple[float, bool]]:
    """Extract ``(claimed confidence, actual success)`` pairs from traces.

    Uses each run's LAST ``done`` call carrying a non-empty ``confidence``
    argument. Ground truth is the verifier verdict; if verification never
    happened (pathological trace), the terminal outcome stands in. Confidence
    values outside [0, 1] are clamped into it (models occasionally overshoot);
    non-numeric or non-finite values exclude the run.
    """
    pairs: list[tuple[float, bool]] = []
    for trace in traces:
        claimed = _last_claimed_confidence(trace)
        if claimed is None:
            continue
        actual = trace.verifier_result.success if trace.verifier_result is not None else trace.success
        pairs.append((claimed, actual))
    return pairs


def _last_claimed_confidence(trace: RunTrace) -> float | None:
    for step in reversed(trace.steps):
        call = step.tool_call
        if call is None or call.name != "done":
            continue
        raw = call.arguments.get("confidence")
        if raw is None:
            continue
        try:
            value = float(raw)  # accepts ints and numeric strings alike
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        return min(1.0, max(0.0, value))
    return None


def compute_calibration_report(
    traces: list[RunTrace],
    n_bins: int = DEFAULT_N_BINS,
) -> CalibrationReport:
    """Build the calibration report (bins + ECE + significance warning)."""
    if n_bins < 1:
        msg = f"n_bins must be >= 1, got {n_bins}"
        raise ValueError(msg)

    pairs = collect_confidence_pairs(traces)
    total = len(pairs)

    bins = [
        CalibrationBin(index=index, lower=index / n_bins, upper=(index + 1) / n_bins)
        for index in range(n_bins)
    ]
    confidence_sums = [0.0] * n_bins
    success_counts = [0] * n_bins

    for confidence, success in pairs:
        # conf == 1.0 belongs to the last bin, not an imaginary n-th one;
        # the epsilon guards exact-decimal edges against FP under-shoot
        # (see _BIN_EDGE_EPSILON).
        index = min(int(confidence * n_bins + _BIN_EDGE_EPSILON), n_bins - 1)
        bins[index].count += 1
        confidence_sums[index] += confidence
        if success:
            success_counts[index] += 1

    ece = 0.0
    for index, bin_ in enumerate(bins):
        if bin_.count == 0:
            continue
        bin_.avg_confidence = confidence_sums[index] / bin_.count
        bin_.success_rate = success_counts[index] / bin_.count
        ece += (bin_.count / total) * abs(bin_.avg_confidence - bin_.success_rate)

    nonempty = sum(1 for bin_ in bins if bin_.count > 0)
    warning: str | None = None
    if total == 0:
        warning = (
            "No run reported a confidence value; calibration (bins and ECE) "
            "is undefined for this suite."
        )
    elif nonempty < LOW_STATISTICS_MIN_NONEMPTY_BINS:
        warning = (
            f"Low statistical significance: only {nonempty} non-empty calibration "
            f"bin(s) from {total} run(s) with confidence. Treat ECE={ece:.4f} as "
            "indicative, not reliable."
        )

    return CalibrationReport(
        n_bins=n_bins,
        bins=bins,
        runs_with_confidence=total,
        ece=ece if total > 0 else None,
        low_statistics_warning=warning,
    )
