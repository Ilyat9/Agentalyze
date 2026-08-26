# =============================================================================
# Agentalyze benchmark harness — self-contained runtime image.
#
# Base image: the OFFICIAL Playwright image for Python. It already ships
# Chromium plus every system library headless Chromium needs (fonts, NSS,
# GBM, ...) — installing those by hand is the classic source of brittle,
# hard-to-debug Docker builds, so we deliberately don't.
#
# Python version note: pyproject.toml declares requires-python >= 3.11.
# Tag v1.62.0-noble ships Python 3.12 on Ubuntu 24.04, which satisfies the
# declared floor — no loosening of the minimum version was necessary.
#
# SECRETS: nothing is baked into the image. providers.yaml only NAMES the
# environment variables that hold API keys; real keys are passed at run time:
#   docker run -e OPENROUTER_API_KEY=... agentalyze compare ...
#   docker run --env-file .env agentalyze compare ...
#
# RESULTS: run artifacts land in /app/results (the Settings default
# ./results resolves against WORKDIR /app). Mount a volume there or they are
# lost together with the container:
#   docker run -v $(pwd)/results:/app/results agentalyze \
#       compare --providers gpt-4o-mini-via-openrouter --category navigation
# =============================================================================

# Версия Playwright ДОЛЖНА совпадать с тегом базового образа: браузеры уже
# лежат в /ms-playwright (PLAYWRIGHT_BROWSERS_PATH), и их build-номер должен
# соответствовать ожиданиям Python-пакета.
ARG PLAYWRIGHT_VERSION=1.62.0

# --- Stage 1: dependency layer (cached unless pyproject.toml changes) -------
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS deps-builder

ARG PLAYWRIGHT_VERSION

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Базовый образ содержит Chromium и системные библиотеки, но НЕ сам
# Python-пакет playwright — ставим его строго той же версии, что и образ.
RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}"

# Copy ONLY dependency metadata first, then install against a placeholder
# package: editing source code below never invalidates this heavy layer.
# NOTE: this image ships system Python WITHOUT the venv module, so packages
# are installed straight into /usr/local/lib/python3.12/dist-packages.
#
# The [api] extra adds the HTTP-service stack (`agentalyze serve`): FastAPI,
# SQLAlchemy/Alembic, slowapi, structlog, prometheus-client. It is inert for
# pure CLI usage but makes THIS image usable as both the CLI tool and the
# production API server without two divergent images.
COPY pyproject.toml README.md LICENSE.md ./
RUN mkdir -p src/agentalyze \
    && echo '"""Placeholder package: exists only to resolve dependencies."""' \
        > src/agentalyze/__init__.py \
    && pip install ".[api]"

# --- Stage 2: minimal runtime -----------------------------------------------
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Bring over the cached dependency layer from the builder stage.
COPY --from=deps-builder /usr/local/lib/python3.12/dist-packages \
     /usr/local/lib/python3.12/dist-packages

# Real source + package metadata; reinstall the actual package over the
# placeholder WITHOUT touching the resolved dependencies (so this layer stays
# cheap and deps stay cached).
COPY src/ ./src/
COPY pyproject.toml README.md LICENSE.md ./
# Schema migrations are RUNTIME data for service mode (`agentalyze serve`
# upgrades to head on startup); they live next to WORKDIR so the app finds
# them regardless of how the package itself was installed.
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
RUN rm -rf /usr/local/lib/python3.12/dist-packages/agentalyze* \
    && pip install --no-deps . \
    && rm -rf ./build

# The task suite is RUNTIME DATA: tasks point at local HTML fixtures that the
# built-in fixture server serves to the browser during evaluation. Bake them
# in and pin the settings variable so the working directory doesn't matter.
COPY fixtures/ ./fixtures/
COPY providers.example.yaml pricing.example.yaml ./
ENV AGENTALYZE_FIXTURES_DIR=/app/fixtures

# Run as non-root: the Playwright image provides the 'pwuser' (uid 1000) for
# exactly this purpose — Chromium's sandbox refuses to run as root. Make sure
# the default artifacts directory exists AND is writable by pwuser even when
# no volume is mounted; on Linux hosts, chown your mounted ./results to uid
# 1000 if writes still fail.
RUN mkdir -p /app/results && chown pwuser:pwuser /app/results
USER pwuser

# Declare the mount point for run artifacts (see header comment).
VOLUME ["/app/results"]

# `agentalyze` is the console-script entry point registered in pyproject.toml
# (Phase 3): container usage reads like CLI usage, no internal module paths:
#   docker run --rm agentalyze compare --providers ... --category ...
ENTRYPOINT ["agentalyze"]

# This is an on-demand CLI tool, NOT a long-lived service: a bare
# `docker run` / `docker compose up` prints the help text and exits cleanly
# instead of hanging like a broken daemon. Subcommands do the real work.
CMD ["--help"]
