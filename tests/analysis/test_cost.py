"""Cost computation: priced, unknown-priced and known-free providers.

Hand-derived reference numbers:
    tokens 2000 prompt / 1000 completion
    prices   0.0003 USD/1k prompt, 0.0006 USD/1k completion
    => cost = (2000/1000)*0.0003 + (1000/1000)*0.0006 = 0.0006 + 0.0006 = 0.0012
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentalyze.analysis.cost import compute_run_cost, summarize_costs
from agentalyze.analysis.metrics import compute_metrics
from agentalyze.analysis.pricing import ModelPrice, PricingConfig, load_pricing
from agentalyze.runner.trace import RunOutcome
from tests.analysis.conftest import PROVIDER_A, PROVIDER_B, make_trace

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pricing() -> PricingConfig:
    return PricingConfig(
        pricing={
            PROVIDER_A: ModelPrice(
                prompt_price_per_1k_usd=0.0003,
                completion_price_per_1k_usd=0.0006,
            ),
            "ollama-local": ModelPrice(free=True),
        }
    )


def _trace(provider: str, prompt_tokens: int = 2000, completion_tokens: int = 1000):
    return make_trace(
        [],
        RunOutcome.SUCCESS,
        provider_name=provider,
        verifier_success=True,
        total_prompt_tokens=prompt_tokens,
        total_completion_tokens=completion_tokens,
    )


class TestRunCost:
    def test_priced_provider_exact_arithmetic(self) -> None:
        cost = compute_run_cost(_trace(PROVIDER_A), _pricing())
        assert cost == pytest.approx(0.0012)

    def test_unknown_provider_is_none_not_zero(self) -> None:
        # No table entry => "we don't know", which must stay None.
        assert compute_run_cost(_trace(PROVIDER_B), _pricing()) is None

    def test_known_free_provider_is_zero_not_none(self) -> None:
        # Local Ollama declared free => exactly $0.0 ("known free"),
        # semantically distinct from the unknown case above.
        cost = compute_run_cost(_trace("ollama-local"), _pricing())
        assert cost == 0.0


class TestAggregation:
    def test_one_unknown_poisons_the_total(self) -> None:
        total, avg = summarize_costs([0.5, None, 0.25])
        assert total is None
        assert avg is None

    def test_all_known_sums_including_free_zeros(self) -> None:
        total, avg = summarize_costs([0.25, 0.0, 0.5])
        assert total == pytest.approx(0.75)
        assert avg == pytest.approx(0.25)

    def test_metrics_with_all_free_provider_is_zero_not_none(self) -> None:
        traces = [_trace("ollama-local"), _trace("ollama-local")]
        metrics = compute_metrics(traces, pricing=_pricing())
        assert metrics.total_cost_usd == 0.0
        assert metrics.avg_cost_usd_per_task == 0.0

    def test_metrics_with_unknown_run_cost_is_none(self) -> None:
        # A suite where one run's cost is unknown (no stored value, no
        # pricing table) must aggregate to None, never to a fake partial sum.
        traces = [
            _trace(PROVIDER_A, prompt_tokens=0, completion_tokens=0),
            _trace(PROVIDER_A, prompt_tokens=0, completion_tokens=0),
        ]
        traces[0].total_cost_usd = 0.5   # first run priced...
        # ...second run's cost stays None ("unknown").
        metrics = compute_metrics(traces)
        assert metrics.total_cost_usd is None
        assert metrics.avg_cost_usd_per_task is None


class TestPricingConfigLoading:
    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "pricing.yaml"
        config_path.write_text(
            "pricing:\n"
            f"  {PROVIDER_A}:\n"
            "    prompt_price_per_1k_usd: 0.001\n"
            "    completion_price_per_1k_usd: 0.002\n"
            "  ollama-local:\n"
            "    free: true\n",
            encoding="utf-8",
        )
        pricing = load_pricing(config_path)
        entry = pricing.price_for(PROVIDER_A)
        assert entry is not None
        assert entry.prompt_price_per_1k_usd == pytest.approx(0.001)
        assert pricing.price_for("ollama-local") == ModelPrice(free=True)
        assert pricing.price_for(PROVIDER_B) is None

    def test_shipped_example_file_is_valid(self) -> None:
        # The example that users copy must itself parse and validate.
        pricing = load_pricing(REPO_ROOT / "pricing.example.yaml")
        assert pricing.price_for("gpt-4o-mini-via-openrouter") is not None
        free_entry = pricing.price_for("llama31-8b-local")
        assert free_entry is not None and free_entry.free

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            ("pricing:\n  p:\n    prompt_price_per_1k_usd: 0.1\n", "only one price set"),
            (
                (
                    "pricing:\n  p:\n"
                    "    prompt_price_per_1k_usd: 0.1\n"
                    "    completion_price_per_1k_usd: 0.2\n"
                    "    free: true\n"
                ),
                "free contradicts explicit prices",
            ),
        ],
    )
    def test_invalid_entries_rejected(self, tmp_path: Path, raw: str, reason: str) -> None:
        config_path = tmp_path / "bad.yaml"
        config_path.write_text(raw, encoding="utf-8")
        with pytest.raises((ValidationError, ValueError)):
            load_pricing(config_path)

    def test_empty_file_gives_empty_table(self, tmp_path: Path) -> None:
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("", encoding="utf-8")
        pricing = load_pricing(config_path)
        assert pricing.pricing == {}
