# =============================================================================
# Dockerfile for the PUBLIC BYOK DEMO on Hugging Face Spaces (sdk: docker).
#
# KEEP IN SYNC with the root Dockerfile: this is the same runtime image, but
# the container BOOTS THE DEMO SERVER instead of the CLI entry point. The
# root Dockerfile must keep `--help` as its bare-run behavior (it is a CLI
# tool), which Spaces cannot override — hence this dedicated small file.
#
# Deployment: create a HF Space whose repo contains (a) this README.md
# (front matter above) and (b) the project tree + this Dockerfile at the
# root. See docs/DEMO_DEPLOYMENT.md for the exact, copy-paste commands.
#
# Free-tier notes:
#  - HF Spaces free CPU Basic: 2 vCPU / 16 GB RAM — enough for headless
#    Chromium (Render's free 512 MB is NOT).
#  - The Space sleeps after ~48h idle; the first request pays a cold start
#    (the demo page warns visitors about this).
#  - Secrets: NONE baked in. Visitors bring their own API keys per request;
#    the server never persists them (that is the whole point of the demo).
# =============================================================================

ARG PLAYWRIGHT_VERSION=1.62.0

# --- Stage 1: dependency layer (cached unless pyproject.toml changes) -------
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS deps-builder

ARG PLAYWRIGHT_VERSION

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}"

COPY pyproject.toml README.md LICENSE.md ./
RUN mkdir -p src/agentalyze \
    && echo '"""Placeholder package: exists only to resolve dependencies."""' \
        > src/agentalyze/__init__.py \
    && pip install ".[api]"

# --- Stage 2: runtime --------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=deps-builder /usr/local/lib/python3.12/dist-packages \
     /usr/local/lib/python3.12/dist-packages

COPY src/ ./src/
COPY pyproject.toml README.md LICENSE.md ./
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
RUN rm -rf /usr/local/lib/python3.12/dist-packages/agentalyze* \
    && pip install --no-deps . \
    && rm -rf ./build

# Task fixtures are runtime data for the demo runs (served by the built-in
# fixture server to the demo's own Chromium).
COPY fixtures/ ./fixtures/
COPY providers.example.yaml pricing.example.yaml ./
ENV AGENTALYZE_FIXTURES_DIR=/app/fixtures

RUN mkdir -p /app/results && chown pwuser:pwuser /app/results
USER pwuser

# --- Demo-mode configuration (the difference from the root Dockerfile) -------
# SQLite in /tmp: the writable location on Spaces (free tier has no volume).
ENV AGENTALYZE_DATABASE_URL=sqlite+aiosqlite:////tmp/agentalyze-demo.db \
    AGENTALYZE_RESULTS_DIR=/tmp/results \
    AGENTALYZE_DEMO_MODE_ENABLED=1 \
    AGENTALYZE_DEMO_RATE_LIMIT="3 per hour" \
    # Memory-frugal Chromium flags for the shared free tier.
    AGENTALYZE_CHROMIUM_LAUNCH_ARGS="--disable-dev-shm-usage,--disable-gpu"

EXPOSE 7860

# Spaces route the app_port (7860, set in README.md front matter) here.
# --demo-mode is the CLI equivalent of AGENTALYZE_DEMO_MODE_ENABLED=1.
ENTRYPOINT ["agentalyze"]
CMD ["serve", "--host", "0.0.0.0", "--port", "7860", "--demo-mode"]
