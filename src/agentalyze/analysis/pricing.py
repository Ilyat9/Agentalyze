"""Editable token-price table for converting token usage into USD.

Prices are deliberately NOT hardcoded in code: they change frequently, and a
stale number baked into source silently misleads every future report. Instead
the table lives in a small YAML file (see ``pricing.example.yaml`` at the
repository root) with the shape::

    pricing:
      <provider_name>:
        prompt_price_per_1k_usd: <float>
        completion_price_per_1k_usd: <float>
      # or, for providers with no real monetary cost (local Ollama):
      <provider_name>:
        free: true

.. warning::
    ALWAYS verify current prices with your provider before using the numbers
    here for real financial decisions — the example file ships illustrative
    values only.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class ModelPrice(BaseModel):
    """Price entry for one provider name.

    Exactly one of the two forms is valid: both per-1k prices, or ``free``.
    Mixing them, or setting neither, is a configuration error caught at load
    time rather than a silent wrong number later.
    """

    prompt_price_per_1k_usd: float | None = None
    completion_price_per_1k_usd: float | None = None
    free: bool = False

    @model_validator(mode="after")
    def _exactly_one_form(self) -> ModelPrice:
        prices_set = (
            self.prompt_price_per_1k_usd is not None or self.completion_price_per_1k_usd is not None
        )
        if self.free and prices_set:
            msg = "free: true contradicts explicit per-token prices"
            raise ValueError(msg)
        if not self.free and (
            self.prompt_price_per_1k_usd is None or self.completion_price_per_1k_usd is None
        ):
            msg = (
                "set BOTH prompt_price_per_1k_usd and completion_price_per_1k_usd, "
                "or free: true"
            )
            raise ValueError(msg)
        return self


class PricingConfig(BaseModel):
    """The whole editable price table, keyed by provider name."""

    pricing: dict[str, ModelPrice] = Field(default_factory=dict)

    def price_for(self, provider_name: str) -> ModelPrice | None:
        """Entry for ``provider_name``, or None when it is absent from the table.

        ``None`` here means "we don't know the price" — semantically distinct
        from a known-free (``free: true``) provider; see ``analysis.cost``.
        """
        return self.pricing.get(provider_name)


def load_pricing(path: Path | str) -> PricingConfig:
    """Load and validate a pricing YAML file (empty file -> empty table)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        msg = f"Pricing config must be a YAML mapping, got {type(raw).__name__}"
        raise TypeError(msg)
    return PricingConfig.model_validate(raw)
