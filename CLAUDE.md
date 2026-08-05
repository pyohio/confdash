# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Conference management for community events, multi-organization and multi-year from the start. The
first feature is video review: organizers match talk videos to talks, speakers review and correct
captions and approve publication.

This replaces the original confdash at `../confdash`, which stays running until the M2 port lands.
Nothing here depends on it at runtime; treat it as reference material only.

## Commands

```bash
just                      # list all recipes
just up / down / bounce   # docker-compose stack; bounce after dependency changes
just status               # what is running, health, and host ports
just follow               # tail logs
just manage <cmd>         # any manage.py command
just migrate / makemigrations
just test                 # full suite
just test-unit            # no database
just test-integration     # needs the database
just lint / just format   # ruff (fmt aliases format)
just check                # lint + format check + tests — run before pushing
```

Project management commands:

```bash
just manage sync_program --event 2026           # pull talks and speakers from the talk source
just manage drain_provider_writes --dry-run     # provider writes that are due, spending no quota
just manage drain_provider_writes               # execute them
```

Django runs in the stack, never on the host. Recipes work from the host, where they exec into the
`app` container, or from inside it. So `just up -d` is a prerequisite for `manage`, `shell`, and the
`test*` recipes: with only postgres up they fail with `service "app" is not running`. `lint` and
`format` are the exceptions and run on the host without the stack.

Single test: `just test src/events/tests/test_models.py::TestEvent::test_series_groups_iterations`

The justfile sets `positional-arguments`, so `manage`, `shell`, and the `test*` recipes forward
arguments via `"$@"` and preserve quoting: `just shell -c "print(1+1)"` works. Recipes that
delegate to another `just` recipe (`migrate`, `makemigrations`, `loaddata`) re-parse their
arguments, so they suit simple flags only.

Outbound mail goes to mailpit in the dev stack, readable at http://localhost:8026. Nothing sends
real email locally.

## Architecture

```
src/
  project/         settings, urls, wsgi/asgi, logging — no application logic
  common/          plain Python and abstract models, NOT a Django app
  accounts/        User (email USERNAME_FIELD, passwordless), LoginToken, auth_method
  events/          Organization, OrganizationMembership, Event, scopes, authz
  integrations/    ProviderConnection, EventProviderBinding, SyncRun, ProviderWrite, outbox
    providers/     capability protocols (base.py) and per-provider adapters
  program/         Talk, Speaker, TalkSpeaker, and the talk-source sync
  videos/          Video, matching, review authz, and the organizer confirm queue
  templates/       one tree: base.html, then <app>/ and <app>/partials/ for HTMX fragments
```

`common/` is intentionally **not** in `INSTALLED_APPS`: Django app → its own app directory, plain
module or abstract model → `common/`. Abstract models need no app registration.

### Tenancy

`Organization` → `Event`, where an `Event` is one iteration (PyOhio 2026). `Event.series` groups
iterations across years. Everything holding conference data has an `event` FK. Scoping is enforced
in application code, not row-level security.

Use `event.resolve_setting(key, default)` for policy: it checks the event, then the organization,
then the default. Presence wins over truthiness, so an event can override `True` with `False`.

### Authentication and authorization

Two audiences, two mechanisms, one `User`. Organizers authenticate against their organization's
identity provider; speakers use emailed magic links. The Django admin is for deployment operators
only: organizers get a purpose-built interface, so `is_staff` is never derived from a group mapping.
Full design in `plans/authentication.md`.

- **Every organizer access check goes through `events.authz`**, never an ad-hoc `OrganizationMembership`
  lookup in a view. It is the single place that requires all three of an allowed auth method, a
  membership in that organization, and the scope.
- **Pass a `Scope` at every call site now**, even though every organizer currently holds every scope.
  Empty `OrganizationMembership.scopes` means unrestricted; owners always hold everything.
- **Every login path must call `accounts.auth_method.set_auth_method()`**, or that session grants no
  organizer access. Deliberate: a session with no recorded mechanism fails closed.
- A magic-link session never grants organizer access, even with a real membership. Otherwise the
  membership row becomes an unrevoked path around the organization's IdP.

**Organizer URLs are path-scoped: `/o/<organization_slug>/<event_slug>/...`**, so the tenant is resolved
and authorized before a view body runs. Use `@organizer_view(Scope.X)` from `events.decorators`; it
consumes both slugs and hands the view an `Event`. Never resolve an event by its slug alone — `2026`
exists in every organization. `require_scope` / `has_scope` cover the in-view and in-template cases.

Nothing sets `AuthMethod.FEDERATED` yet, so **no organizer URL is reachable by a real browser session**
until organizer SSO lands. Tests mint the session via the `as_federated` fixture.

### The provider abstraction

This is the load-bearing design decision. The app depends on **capabilities**
(`talk_source`, `ticketing`, `video_host`), never on Pretalx, Tito, or YouTube.

**Nothing outside `integrations/providers/` may import a provider module or branch on a provider
name.** Application code does:

```python
adapter = resolve_adapter(event, Capability.TALK_SOURCE)
talks = adapter.fetch_talks()
```

Adding a provider is a new module under `providers/`, decorated with `@register`, imported in
`providers/__init__.py`. No migration, no change to application code.

Adapters are thin: httpx to a remote API, returning the provider-neutral dataclasses from
`providers/base.py`. **Adapters never touch the ORM** — sync services own all persistence. So a
provider bug cannot corrupt local state, and adapters are testable with recorded fixtures.

Credentials live on org-scoped `ProviderConnection`; `EventProviderBinding` selects one per
capability and adds event-specific config. An org can hold several connections for the same
provider, since Pretalx issues credentials per event: uniqueness is `(organization, slug)`.

### Synced data is mirrored locally

`Talk`, `Speaker`, `Video` are local canonical models populated by sync, with provider IDs in
separate `external_id` fields — never as primary keys. Syncs are **idempotent upserts keyed on
`(event, external_id)`, and never delete on absence**: a provider returning a short list must not
wipe local rows and their review state.

### Writes to a provider go through the outbox

Reads may call an adapter directly; **writes may not**. A failed read costs a wait, a failed write
leaves the database asserting something untrue about the provider with nothing to detect it. Full
design in `plans/provider-writes.md`.

- **Local state records what the provider confirmed. Intent lives in `ProviderWrite`.** Never set
  `publication_state` or `privacy_status` because a write was requested; only because one succeeded.
- **Enqueue in the same transaction as the local change** that motivated it, via
  `integrations.outbox.enqueue` or an app's wrapper (`videos.writes.request_*`).
- **Guards are re-checked in the handler, at execution.** An approval can be withdrawn and a talk
  marked `do_not_record` between enqueue and drain.
- `integrations.outbox` owns the queue and must not import an app that owns data. Apps register a
  handler per `(capability, operation)` from their `AppConfig.ready`, as `videos.writes` does.
- Draining is serial and stops the batch on `QuotaExceeded`, because every rejected call still costs a
  YouTube unit.

## Conventions

- Subclass `common.models.BaseModel` for new models. UUIDv7 PKs, never sequential integers: IDs
  appear in URLs emailed to speakers.
- `JSONField` is the standard tool for structured-but-variable data: `User.data`,
  `Organization.settings` / `Event.settings`, provider `config`, `SyncRun.counts`,
  `ProviderWrite.desired`. Anything needing a query, a constraint, or validation graduates to a real
  column. Note there is deliberately **no** `raw` provider-payload field anywhere; see
  `plans/decisions.md`.
- `settings.TESTING` (`"pytest" in sys.modules`) is the test sentinel. There is no test settings
  module — the suite runs against the same settings production does.
- `django-ninja` for any API, never DRF. `httpx` for outbound HTTP, never requests.
- Email goes through **django-anymail**, provider chosen by `EMAIL_PROVIDER` (Mailgun for the
  PyOhio instance). Never import a provider backend directly. Sender identity is per deployment:
  let mail default to `DEFAULT_FROM_EMAIL` rather than passing a From address, and do not add
  per-organization sender fields — see `plans/decisions.md` for why that was removed.
- `django-typer` for management commands.
- structlog with keyword events: `logger.info("program.synced", event_slug=..., talks=12)`.
- Server-rendered templates with HTMX; minimal Alpine.js; DaisyUI + Tailwind via CDN, no JS build.
  CDN assets are pinned and carry a `sha384` integrity hash; compute a real one rather than inventing
  it, since a wrong hash silently blocks the asset. Tailwind's Play CDN is the one exception and cannot
  take a hash: see `plans/issues.md`.
- HTMX mutations are POST-only and return the replaced fragment, not a whole page. Decorate views
  `@organizer_view(...)` **outermost** and `@require_POST` inside it, so an outsider gets 403 rather
  than the 405 that would confirm the endpoint exists.
- Single `settings.py`. Environment differences via env vars and `if DEBUG`, never split settings.
- Secrets required in production hard-fail when `DEBUG=False`; insecure fallbacks only under
  `DEBUG=True`.
- Line length 120. Ruff only — no Black, no pre-commit.
- Migrations are excluded from ruff. Review generated migrations before committing.
- **Migrate forward. Do not rewrite or regenerate an existing migration.** While the models are still
  settling, a model change gets a new migration, however small. Regenerating one that has already been
  applied leaves the database describing something no migration accounts for, which then has to be
  reconciled by hand. The accumulated migrations get collapsed once before the first live deployment,
  which is the right moment for it and the only one.

## Security invariants

Do not weaken these without discussion; they are why the credential design looks the way it does.

- **Provider credentials are encrypted at rest** and read only through
  `ProviderConnection.get_credentials()`. Never add a plaintext credential field, never expose
  credentials as readable text in the admin, never put them in a fixture.
- **Never log a secret.** `project/logging.py` redacts sensitive keys at any nesting depth, and
  `src/project/tests/test_logging.py` guards it. Add new secret key names to `SENSITIVE_KEYS`.
- **`FIELD_ENCRYPTION_KEY` loss is unrecoverable** — every org's credentials go with it.
- **Magic-link tokens are stored hashed**, single-use, and expiring.
- **Cross-organization access must be impossible.** `EventProviderBinding.clean()` rejects a
  connection from another organization, and `events.authz` filters every membership lookup by
  organization; neither rule can be a DB constraint, so both need their tests.
- **Organizer access requires a federated (or operator password) session.** Do not widen the
  allow-list in `accounts.auth_method` to admit magic links.

## Testing

- pytest + pytest-django. `pythonpath = ["src"]`, `filterwarnings = ["error"]`.
- **Every test must carry exactly one of the `unit` or `integration` markers.** CI runs unit tests
  with no database, so a mismarked test fails there. Prefer per-test markers over module-level
  `pytestmark` in files that mix both.
- Plain fixtures and factory functions calling real service-layer code. No model-bakery or
  factory_boy.
- Provider adapters: recorded JSON response fixtures under
  `src/integrations/tests/fixtures/<provider>/`. No live network in the suite.
- Sync services and the resolver: the fake adapters in `integrations/tests/fakes.py`, registered
  by the `fake_providers` fixture, which snapshots and restores the registry.

## Workflow

- Design docs go in `plans/` before the code. One file per substantial feature, plus `issues.md`
  and `nits.md`. Prune entries as work lands so `plans/` stays an accurate to-do list.
- `docs/` is reference material (things that are true), `plans/` is forward-looking.
- Read `plans/decisions.md` before revisiting an architectural choice: it records what was
  rejected and why.
- Conventional Commits. PRs are squash-merged, so the PR title becomes the commit on main and
  drives versioning — a CI job lints it.
- Version bumps are a manual GitHub Actions workflow, never automatic on merge.
- Do not commit, branch, push, or open a PR unless explicitly asked.

## Not using

Deliberate choices, not oversights:

- **DRF** — django-ninja instead.
- **requests** — httpx instead.
- **Celery** — django-tasks (Django 6 native) if background jobs are needed.
- **model-bakery / factory_boy** — plain fixtures and factory functions.
- **pre-commit** — `just check` locally, CI is the real gate.
- **Black** — ruff format.
- **Split settings** — one `settings.py`.
- **Row-level security** — app-level scoping.
- **A type checker** — not yet. Revisit if the codebase outgrows what tests and review catch.
- **Provider names in application code** — capabilities and the registry instead.
