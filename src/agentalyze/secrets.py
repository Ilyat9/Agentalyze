"""Secret resolution with an optional external secrets backend.

The historical (and still default) mechanism is plain environment variables:
``providers.yaml`` names a variable, the value lives in the process env. That
stays untouched — for CLI usage and simple self-hosted deployments it remains
the right amount of machinery.

For service deployments this module adds ONE optional layer on top:
`AGENTALYZE_VAULT_ADDR` pointing at a HashiCorp Vault KV-v2 mount. When set,
:meth:`resolve_secret` first asks Vault for ``{mount}/data/{env_var_name}``
(the env var name doubles as the KV secret path, so ``providers.yaml`` needs
no schema change), falling back to the process environment when Vault has no
such secret or is unreachable at all. A missing/failed Vault therefore
degrades to exactly the pre-existing behavior instead of breaking startup.

Deliberately NOT implemented: dynamic DB credentials, lease renewal,
AppRole auth (token-only via the standard VAULT_TOKEN env var). Those are
infrastructure concerns that belong to the deployment, not this codebase.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agentalyze.config import Settings

logger = logging.getLogger(__name__)


class SecretResolutionError(Exception):
    """Vault was explicitly configured but refused the request."""


def _vault_read(
    settings: Settings, path: str, timeout_seconds: float = 5.0
) -> dict[str, Any] | None:
    """Read one KV-v2 secret's data mapping; None when absent/unreachable."""
    import httpx  # lazy: keeps httpx import cost out of pure-CLI paths

    token = os.environ.get(settings.vault_token_env_var, "")
    url = f"{settings.vault_addr.rstrip('/')}/v1/{settings.vault_kv_mount}/data/{path}"
    headers = {"X-Vault-Token": token} if token else {}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        logger.warning("Vault unreachable at %s (%s); falling back to env vars",
                       settings.vault_addr, type(exc).__name__)
        return None
    if response.status_code == 404:
        return None  # not stored in Vault -> env fallback, by design
    if response.status_code != 200:
        raise SecretResolutionError(
            f"Vault returned HTTP {response.status_code} for secret {path!r}"
        )
    data: dict[str, Any] | None = response.json().get("data", {}).get("data")
    return data


def resolve_secret(name: str, settings: Settings) -> str | None:
    """Resolve secret ``name`` (an env-var name) from Vault-then-env.

    Returns None when the secret exists nowhere; callers turn that into their
    own actionable configuration error.
    """
    if settings.vault_addr:
        data = _vault_read(settings, name)
        if data is not None:
            value = data.get("value")
            if isinstance(value, str) and value:
                return value
            raise SecretResolutionError(
                f"Vault secret {name!r} must contain a non-empty 'value' key"
            )
    return os.environ.get(name) or None
