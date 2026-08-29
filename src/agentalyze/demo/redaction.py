"""Secret redaction: THE earliest-line-of-defense against key leakage.

The public demo accepts provider API keys from anonymous visitors. This
module answers the two questions that matter for that trust model:

1. WHEN is the key masked?  — The demo endpoint registers the key in this
   module's registry the moment it is extracted from the raw request body,
   BEFORE any validation, provider construction or browser work happens.
2. WHERE is it masked?      — EVERYWHERE logs are produced: this module's
   ``redaction_processor`` sits in the GLOBAL structlog pipeline (see
   ``api/observability.py``), AFTER ``format_exc_info`` — so event payloads,
   bound fields AND fully rendered exception tracebacks (including uvicorn's
   own error logs, which are routed through the same pipeline) get any
   registered secret replaced with ``[REDACTED]`` before a single byte is
   written to the output stream.

Deliberate design limits (documented, not hidden):

* The registry is process-local memory. A secret is registered on request
  arrival and ``unregister_secret``-ed in the endpoint's ``finally`` — a key
  outlives its request by at most a lost race between coroutines, never
  touches disk, and is never persisted anywhere.
* Redaction is string-replacement based. It cannot catch a key split across
  two log fields or re-encoded (e.g. base64). The demo therefore also keeps
  the key OUT of every structure it builds (no echoes in responses, no key
  in trace artifacts) — masking here is the safety net, not the strategy.
"""

from __future__ import annotations

from typing import Any

from structlog.typing import EventDict

#: Placeholder written wherever a registered secret is found.
REDACTED = "[REDACTED]"

#: Secrets shorter than this are never substituted: a 1-3 char "secret" would
#: corrupt unrelated log text (e.g. replacing "a") without any real benefit —
#: no real provider key is that short.
_MIN_REDACTABLE_LENGTH = 4

_registry: set[str] = set()


def register_secret(value: str) -> None:
    """Start masking ``value`` in every log line until :func:`unregister_secret`.

    Called by the demo endpoint at the EARLIEST possible moment — right after
    the key is read from the raw request body, before anything else runs.
    """
    if value:
        _registry.add(value)


def unregister_secret(value: str) -> None:
    """Stop masking ``value``; called in the endpoint's ``finally`` block."""
    _registry.discard(value)


def registered_secrets() -> frozenset[str]:
    """Currently registered secrets (test/debug introspection only)."""
    return frozenset(_registry)


def redact_text(text: str) -> str:
    """Return ``text`` with every registered secret replaced by ``[REDACTED]``."""
    for secret in _registry:
        if len(secret) >= _MIN_REDACTABLE_LENGTH and secret in text:
            text = text.replace(secret, REDACTED)
    return text


def redact_deep(obj: Any) -> Any:
    """Recursively scrub strings inside dicts / lists / tuples / bytes."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, bytes):
        return redact_text(obj.decode("utf-8", errors="replace")).encode(
            "utf-8", errors="replace"
        )
    if isinstance(obj, dict):
        return {key: redact_deep(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_deep(item) for item in obj]
    return obj


def redaction_processor(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: mask registered secrets in every rendered event.

    Signature matches ``structlog.typing.Processor``. With an empty registry
    (pure-CLI usage, non-demo service mode) this is a zero-cost no-op.
    """
    result: EventDict = redact_deep(dict(event_dict))
    return result
