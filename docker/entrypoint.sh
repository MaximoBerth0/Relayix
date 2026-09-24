#!/bin/sh
set -eu

# one-off commands, e.g. the migration task: alembic upgrade head
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# off on Fargate, migrations run as their own task so replicas never race
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[entrypoint] applying database migrations..."
    alembic upgrade head
fi

WORKERS="${WEB_CONCURRENCY:-4}"
echo "[entrypoint] starting uvicorn with ${WORKERS} worker(s)..."

# proxy headers are trusted because only the ALB can reach 8000 (security group)
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WORKERS}" \
    --proxy-headers \
    --forwarded-allow-ips '*' \
    --timeout-keep-alive 65 \
    --timeout-graceful-shutdown 25
