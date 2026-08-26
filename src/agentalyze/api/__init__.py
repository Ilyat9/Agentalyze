"""HTTP service layer for Agentalyze.

This package is deliberately OPTIONAL: the pure-CLI usage (single user,
filesystem JSON storage) keeps working with nothing here installed. The
service layer adds, as thin wrappers over the SAME orchestration functions
the CLI calls (:func:`agentalyze.orchestration.suite_runner.run_suite`,
:func:`agentalyze.regression.diff.compute_regression`,
:func:`agentalyze.orchestration.report.render_report`):

* ``db``          — persistent metadata store (SQLAlchemy + Alembic migrations)
* ``auth``        — hashed API keys (Bearer tokens)
* ``metrics``     — Prometheus instrumentation incl. provider-call tracking
* ``observability``— structlog-based JSON/console logging setup
* ``service``     — background suite-run execution manager
* ``app``         — the FastAPI application factory
"""

from agentalyze.api.app import create_app

__all__ = ["create_app"]
