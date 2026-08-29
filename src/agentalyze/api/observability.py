"""Structured logging setup.

Default stays human-readable console output (the historical CLI behavior);
AGENTALYZE_LOG_FORMAT=json switches every record to one-JSON-object-per-line
via structlog, suitable for Loki/ELK/any aggregator without vendor lock-in.
stdlib logging (used across the codebase) is routed through the same
pipeline so BOTH the CLI-era loggers and new service-layer loggers emit a
single consistent format.
"""

from __future__ import annotations

import logging
import sys

import structlog

from agentalyze.demo.redaction import redaction_processor


def configure_logging(level: str = "INFO", json_format: bool = False) -> None:
    """Idempotent global logging configuration for both CLI and service."""
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Placed AFTER format_exc_info so it also scrubs the fully rendered
        # traceback string. With an empty secret registry this is a no-op, so
        # it is safe (and deliberate) to have it in the GLOBAL pipeline: demo
        # secrets must be masked in EVERY log line, not only in demo-emitted
        # ones (uvicorn tracebacks, third-party warnings, etc.).
        redaction_processor,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_format
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace handlers on reconfiguration (e.g. tests calling this repeatedly).
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Uvicorn's own loggers must not double-print through their default handlers.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True
