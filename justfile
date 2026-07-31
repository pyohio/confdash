# Task interface for local development. Recipes front docker-compose so that the database and
# app are always the same versions everyone else is running.
#
# Recipes detect whether they are already inside the app container, so `just migrate` works
# both from the host (exec into the container) and from a shell inside it.

# Pass recipe arguments to the shell as real positional parameters, so recipes can use "$@" and
# preserve the caller's quoting. Without this, `{{ARGS}}` interpolation flattens arguments into a
# single unquoted string and `just manage shell -c "print(1+1)"` fails to parse.
set positional-arguments

_default:
    @just --list

export IS_CONTAINER := `[ -f "/.dockerenv" ] && echo "true" || echo "false"`

# Fail fast when a host-only recipe is run inside the container.
[private]
host-only:
    #!/usr/bin/env sh
    if [ "$IS_CONTAINER" = "true" ]; then
        echo "This recipe must be run on the host, not inside the container." >&2
        exit 1
    fi

# Prefix that runs a command in the app container, or nothing when already inside it.
#
# A variable rather than a private recipe so that argument-forwarding recipes stay a single shell
# command. Recipes that need to preserve the caller's quoting use this with "$@"; recipes that
# delegate to another `just` recipe re-parse their arguments and so only suit simple flags.
EXEC := if IS_CONTAINER == "true" { "" } else { "docker compose exec app" }

# Build and start the stack. Postgres is healthcheck-gated, so the app waits for a ready
# database rather than crash-looping on startup.
up *ARGS:
    just host-only
    docker compose up --build {{ARGS}}

# Stop the stack, leaving the database volume intact.
down *ARGS:
    just host-only
    docker compose down {{ARGS}}

# Rebuild images without starting anything.
build *ARGS:
    just host-only
    docker compose build {{ARGS}}

# Full restart from scratch. Use after changing Dockerfiles or dependencies.
bounce *ARGS:
    just host-only
    just down {{ARGS}}
    just up -d {{ARGS}}

# Restart one service, e.g. `just restart app`.
restart *ARGS:
    just host-only
    docker compose restart {{ARGS}}

# Show recent logs from all services.
logs *ARGS:
    just host-only
    docker compose logs {{ARGS}}

# Tail logs. The usual way to watch the dev server reload.
follow *ARGS:
    just host-only
    docker compose logs --follow {{ARGS}}

# Run any manage.py command, e.g. `just manage createsuperuser`.
manage *ARGS:
    @{{EXEC}} python src/manage.py "$@"

# Apply migrations.
migrate *ARGS:
    just manage migrate {{ARGS}}

# Generate migrations. Review the output before committing; these are not auto-formatted.
makemigrations *ARGS:
    just manage makemigrations {{ARGS}}

createsuperuser *ARGS:
    just manage createsuperuser {{ARGS}}

# Django shell. Direct rather than via `manage` so `just shell -c "print(1+1)"` keeps its quoting.
shell *ARGS:
    @{{EXEC}} python src/manage.py shell "$@"

# Postgres shell.
dbshell *ARGS:
    just manage dbshell {{ARGS}}

# Load a fixture, e.g. `just loaddata dev_seed`.
loaddata +ARGS:
    just manage loaddata {{ARGS}}

# Full test suite.
test *ARGS:
    @{{EXEC}} pytest "$@"

# Unit tests only: no database, fast enough to run on every save.
test-unit *ARGS:
    @{{EXEC}} pytest -m unit "$@"

# Integration tests: need the database.
test-integration *ARGS:
    @{{EXEC}} pytest -m integration "$@"

# Test suite with a coverage report.
coverage *ARGS:
    @{{EXEC}} pytest --cov --cov-report=term-missing "$@"

# Lint. Runs on the host so it works without the stack up.
lint *ARGS:
    uv run ruff check {{ARGS}}

# Format in place.
format *ARGS:
    uv run ruff format {{ARGS}}
    uv run ruff check --fix {{ARGS}}

alias fmt := format

# The local gate before pushing. CI is the real gate.
check:
    uv run ruff check
    uv run ruff format --check
    just test

# Django's own configuration checks, including the production-readiness subset.
django-check *ARGS:
    just manage check {{ARGS}}

# Preview the next version and changelog entry without writing anything.
version-bump-preview:
    uv run cz bump --dry-run

# Bump version, update CHANGELOG, and tag. Prefer the GitHub Actions workflow for releases.
version-bump *ARGS:
    uv run cz bump {{ARGS}}
