# Built by CI (arm64) and pushed to ECR, Fargate pulls it. Context is the repo root:
#   docker build -f docker/Dockerfile -t relayix-api .
#
# Two stages: the builder owns Poetry and the compilers, the runtime gets the
# resulting virtualenv and nothing else.

ARG PYTHON_VERSION=3.14

# builder
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

WORKDIR /app

# asyncpg ships C extensions and there is no guarantee of a wheel for every
# interpreter, so keep a compiler around in this stage
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

# only the two files that decide the dependency set, so a source-only change
# reuses this layer instead of resolving everything again
COPY pyproject.toml poetry.lock ./

# locked runtime deps into /app/.venv. No dev group, no project root
RUN poetry install --only main --no-root

# runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    WEB_CONCURRENCY=4

# makes the commit behind a running tag recoverable from the image
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.source="https://github.com/MaximoBerth0/Relayix" \
      org.opencontainers.image.description="Relayix API" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /app

# tini reaps the uvicorn workers and forwards signals, without it a SIGTERM
# from `docker stop` never reaches them and the stop takes the full timeout
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

# the virtualenv only, no compilers, no Poetry, no apt cache
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

USER appuser

EXPOSE 8000

# local only, ECS ignores it and the ALB checks /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else sys.exit(1)"]

ENTRYPOINT ["tini", "--", "./entrypoint.sh"]
