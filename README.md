# confdash

Conference management for community events. Multi-organization and multi-year from the start: one
deployment serves several organizations, each running events across many years.

The first feature is **video review**: organizers pull talk videos from a video host, match them
to talks, and invite speakers; speakers review their video, correct captions, and approve
publication. See [plans/video-review.md](plans/video-review.md).

External services are pluggable. The app depends on *capabilities* (`talk_source`, `ticketing`,
`video_host`), not on Pretalx, Tito, or YouTube. Each event binds a capability to a provider, so
one event can use Pretalx and Tito while another uses something else entirely. See
[plans/provider-integrations.md](plans/provider-integrations.md).

## Status

Bootstrap (M0). The tenancy foundation and provider abstraction exist; no feature code yet.
Concrete provider adapters arrive with M1. Roadmap in [plans/README.md](plans/README.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/), [just](https://just.systems),
[direnv](https://direnv.net), and Docker.

```bash
cp .env.example .env     # works as-is for local development
direnv allow
just up                  # builds images, starts postgres + app, applies migrations
```

The app is at http://localhost:8000, the admin at http://localhost:8000/admin/, and captured
outbound mail at http://localhost:8026 (mailpit). Nothing sends real email in development.

Django runs in the stack rather than on the host, so `just` recipes exec into the `app` container
(they also work when run from inside it). Only what needs reaching from a browser or a host tool is
published: `APP_PORT`, `MAILPIT_UI_PORT`, and `POSTGRES_PORT`, all overridable in `.env` for running
this stack alongside another project's.

Create a login for yourself:

```bash
just createsuperuser
```

Development uses insecure fallbacks for `SECRET_KEY` and `FIELD_ENCRYPTION_KEY` so a fresh
checkout runs with no configuration. Both hard-fail startup when `DEBUG=False`, so a production
deploy cannot accidentally run with them.

## Common commands

`just` with no arguments lists everything. The ones you will actually use:

```bash
just up / down / bounce      # stack lifecycle; bounce after dependency changes
just follow                  # tail logs
just manage <command>         # any manage.py command
just migrate / makemigrations
just shell / dbshell
just test                    # full suite
just test-unit               # no database, fast
just test-integration        # needs the database
just lint / just format      # ruff; `fmt` aliases format
just check                   # lint + format check + tests, the local gate
```

Recipes work both from the host and from inside the app container.

## Layout

```
src/
  manage.py
  project/           settings, urls, wsgi/asgi, logging — no application logic
  common/            BaseModel: UUIDv7 PK, created_at/updated_at (not a Django app)
  accounts/          User (email as username, passwordless), LoginToken
  events/            Organization, OrganizationMembership, Event
  integrations/      ProviderConnection, EventProviderBinding, SyncRun
    providers/       capability protocols and per-provider adapters
  templates/         single tree, organized by app, partials/ for HTMX fragments
plans/               forward-looking design docs, issues.md, nits.md
```

Reference docs get a `docs/` tree once there is any reference material; right now everything
forward-looking lives in `plans/`.

## Data model

Two-level tenancy: `Organization` → `Event`, where an `Event` is one iteration (PyOhio 2026).
`Event.series` groups iterations for year-over-year reporting.

Credentials for external providers are held per organization on `ProviderConnection`, encrypted
at rest, and selected per event by `EventProviderBinding`:

```
Organization "PyOhio"
  ProviderConnection  pretalx-pyohio-2026   capability=talk_source  provider=pretalx
  ProviderConnection  tito-pyohio           capability=ticketing    provider=tito
  ProviderConnection  youtube-pyohio        capability=video_host   provider=youtube

Event "PyOhio 2026"
  EventProviderBinding  talk_source  -> pretalx-pyohio-2026  config={"event_id": "pyohio-2026"}
  EventProviderBinding  video_host   -> youtube-pyohio       config={"playlist_id": "PL..."}
```

An organization can hold several connections for the same provider, because some providers issue
credentials per event — Pretalx does.

Full specification in [plans/data-model.md](plans/data-model.md).

## Security notes

`FIELD_ENCRYPTION_KEY` encrypts every organization's provider credentials. Losing it means
losing all of them, with no recovery: back it up before going live. Credentials are write-only
through the admin, redacted in logs by a structlog processor, and stored as ciphertext so they
are not readable in a database dump.

## Documentation

- [plans/README.md](plans/README.md) — roadmap and index of design docs
- [plans/decisions.md](plans/decisions.md) — bootstrap decisions, rationale, and what was deferred
- [plans/data-model.md](plans/data-model.md) — schema specification
- [plans/provider-integrations.md](plans/provider-integrations.md) — capability/adapter architecture
- [plans/video-review.md](plans/video-review.md) — M1
- [plans/confdash-port.md](plans/confdash-port.md) — M2, porting the legacy project
- [CLAUDE.md](CLAUDE.md) — conventions and architecture for working in this repo

## Relationship to the legacy project

This replaces the original confdash (`../confdash`), which stays running until the port in M2
lands. Nothing here depends on it at runtime.
