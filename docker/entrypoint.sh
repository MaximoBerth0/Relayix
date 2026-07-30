#!/bin/sh
# Applies pending migrations, then hands off to the container's command.
# Set RUN_MIGRATIONS=false to skip
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi

exec "$@"
