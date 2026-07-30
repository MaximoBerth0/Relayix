# Local development image. Dependencies are installed into the system
# site-packages so `alembic` and `uvicorn` are on PATH.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

# libpq/asyncpg and tiktoken ship wheels, build-essential is kept for the
# occasional source-only dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry

# dependency layer: only invalidated when the manifests change
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
