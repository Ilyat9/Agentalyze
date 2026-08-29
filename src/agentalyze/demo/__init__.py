"""Public-demo surface (BYOK): browser-facing website + per-request providers.

This package is SPECIFIC to the publicly deployed portfolio demo and is NOT
part of Agentalyze-the-tool. Its defining difference from the rest of the
codebase is the trust model: the provider API key arrives PER REQUEST from an
anonymous visitor in the request body, not once at startup from
``providers.yaml`` + env vars (``providers/factory.py`` deliberately does NOT
serve this path — the two models must never be conflated).

Modules:

* ``redaction`` — secret masking applied to every log line (structlog
  processor) and to anything user-visible that could carry a key;
* ``tasks``    — the explicit demo-task allowlist (1-3 cheap, easy tasks);
* ``routes``   — the FastAPI router (GET /demo, GET /demo/tasks, POST /demo/run).

Everything here is opt-in via ``Settings.demo_mode_enabled`` (default False);
see docs/DEPLOYMENT.md for self-hosting and docs/DEMO_DEPLOYMENT.md for the
public-demo deployment and its threat model.
"""

from agentalyze.demo.redaction import (
    redact_text,
    register_secret,
    unregister_secret,
)

__all__ = ["redact_text", "register_secret", "unregister_secret"]

