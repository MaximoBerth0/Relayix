#!/bin/sh
set -eu

# One api container per box for now: migrations run here because nothing else
# would apply them. Set RUN_MIGRATIONS=false before running a second replica
# against the same database, otherwise two of them race the alembic_version row.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying database migrations..."
    alembic upgrade head
fi

WORKERS="${WEB_CONCURRENCY:-4}"

echo "[entrypoint] starting uvicorn with ${WORKERS} worker(s)..."

# --proxy-headers with a wildcard trust list is only safe because 8000 is
# never published to the host: the sole route to this port is the Caddy
# container on the edge network, and Caddy overwrites X-Forwarded-For on
# every hop.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WORKERS}" \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --timeout-keep-alive 5 \
    --timeout-graceful-shutdown 25
