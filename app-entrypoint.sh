#!/usr/bin/env sh
# Entrypoint contract: validate configuration, optionally migrate, then exec the command.
#
# Migrations are opt-in via RUN_MIGRATIONS so that scaling to more than one instance does not
# mean several containers racing to migrate the same database.

set -eu

# Development only. The dev stack keeps the virtualenv in a named volume so the host cannot
# shadow it, which means a rebuilt image does not by itself update the venv: after a dependency
# change the container would boot against a stale environment. Reconciling with uv.lock on start
# makes that self-healing. Never enabled in production, where the image is the environment and uv
# is not installed.
if [ "${SYNC_DEPENDENCIES:-false}" = "true" ]; then
    echo "entrypoint: syncing dependencies from uv.lock"
    uv sync --locked
fi

echo "entrypoint: running django checks"
django-admin check

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "entrypoint: applying migrations"
    python src/manage.py migrate --noinput
else
    echo "entrypoint: skipping migrations (RUN_MIGRATIONS is not 'true')"
fi

echo "entrypoint: starting: $*"
exec "$@"
