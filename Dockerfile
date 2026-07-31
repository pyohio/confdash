# Production image. Multi-stage so the runtime layer carries no build tooling.
#
# The dependency install is split from the project install: dependencies change rarely and the
# source changes constantly, so a source edit must not invalidate the dependency layer.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies only. --no-install-project keeps this layer cached across source changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --extra prod

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra prod


FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    DJANGO_SETTINGS_MODULE=project.settings

# curl is for the healthcheck; libpq is needed even with psycopg[binary] for some builds.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

# Static files are baked into the image so no runtime volume or collectstatic step is needed.
# Placeholder values let the build run without real secrets; nothing here reaches runtime.
RUN SECRET_KEY=build-only \
    FIELD_ENCRYPTION_KEY=cbrN3vfnpxYpMHRA_gN3dcbXWv-K5S8VQmM3ZzDcXtA= \
    DEBUG=False \
    DATABASE_URL=postgres://build:build@localhost/build \
    python src/manage.py collectstatic --noinput \
    && chown -R app:app /app/staticfiles

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/healthz/ || exit 1

# Gunicorn treats SIGINT as a graceful shutdown; Docker's default SIGTERM is a quick one. This
# lets in-flight requests finish when a container is replaced.
STOPSIGNAL SIGINT

ENTRYPOINT ["/app/app-entrypoint.sh"]

CMD ["gunicorn", "project.wsgi:application", "-c", "/app/gunicorn.conf.py"]
