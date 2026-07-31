#!/usr/bin/env sh
# Entrypoint contract: validate configuration, optionally migrate, then exec the command.
#
# Migrations are opt-in via RUN_MIGRATIONS so that scaling to more than one instance does not
# mean several containers racing to migrate the same database.

set -eu

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
