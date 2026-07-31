# Bootstrap decisions

Decisions made at project start, with rationale. Things that are settled and unlikely to be
revisited should eventually graduate to `CLAUDE.md` or `docs/`; this file is the record of
*why*, including for the options not taken.

The living record of stack, layout, and quality-gate conventions is [CLAUDE.md](../CLAUDE.md);
this file explains the choices behind them. Both derive from the personal Django standard
documented outside this repo at `~/checkouts/metarepo/tmp/django-patterns/`.

## Resolved

### Naming

Project and Django settings package are both `confdash`. The working directory is `cd1` and
the legacy project stays at `../confdash` for now; it gets sunset once the port in M2 lands.
Nothing in this repo references the legacy project at runtime.

### Tenancy: Organization -> Event

Two levels. `Event` is one iteration of a conference (PyOhio 2026), scoped to an
`Organization`. An optional `series` slug on `Event` groups iterations for year-over-year
reporting.

Rejected: a three-level `Organization -> ConferenceSeries -> Edition`. It is the more correct
model for an org running several distinct conferences, but it is ~1:1 with `Organization` for
every foreseeable tenant and adds a join to every event-scoped query. The `series` field
covers the reporting need without the table. If a tenant ever runs genuinely distinct
conference series, `series` promotes to a FK without disturbing the `Event`-scoped models
that make up the bulk of the schema.

This also matches the legacy `Event` semantics (one row per year), so the M2 port is a remap
rather than a restructure.

### External services are capabilities with pluggable providers

The app does not depend on Pretalx, Tito, or YouTube. It depends on three *capabilities*, and
an event binds each capability to a provider:

| Capability | v1 provider | Plausible alternatives |
| --- | --- | --- |
| `talk_source` | Pretalx | Sessionize, PaperCall, manual entry |
| `ticketing` | Tito | Eventbrite, manual import |
| `video_host` | YouTube | Vimeo, self-hosted |

Adding a provider is a new adapter class plus a row, never a migration. See
[provider-integrations.md](provider-integrations.md).

This is the load-bearing decision of the project. PyOhio's current stack is one
configuration, not the architecture.

### Credentials: app-owned, org-scoped, event-selected

`ProviderConnection` is owned by an `Organization` and holds the credentials, encrypted at
rest. `EventProviderBinding` selects one connection per capability for an event and carries
the event-specific config (which Pretalx event, which YouTube playlist).

An org may hold **several connections for the same provider**, identified by a slug: Pretalx
issues API credentials per event, so PyOhio has `pretalx-pyohio-2026`, `pretalx-pyohio-2027`,
and so on. Uniqueness is therefore `(organization, slug)`, never `(organization, provider)`.
Connections are long-lived rows, not per-event rows, and an event picks the one it needs; a
credential that happens to be scoped to one year is just a connection that only one event
binds.

Rejected: env-referenced secrets, where the DB stores an env var name and an operator injects
the value. Simpler and needs no crypto, but onboarding another org would require an operator
edit and a redeploy. Since the point of the tenancy work is to let other events use this,
forcing a deploy per tenant defeats it. An org configures its own connections; its events
choose among them.

Consequence: `FIELD_ENCRYPTION_KEY` is a required secret, key rotation is a real operational
concern, and credentials must never appear in `dumpdata` output or logs. Tracked in
[provider-integrations.md](provider-integrations.md).

### Synced data is mirrored locally, not read through

`Talk`, `Speaker`, and `Video` are local canonical models populated by sync, keyed on internal
UUIDv7 PKs with provider identifiers stored as separate external-reference fields. Review
state attaches to local rows, the app stays usable when a provider is down or an org's trial
ends, and a provider swap does not orphan review history.

### Line length 120

Going-forward standard. Legacy confdash uses 88; no attempt is made to stay consistent with it.

### Organizer surface is the admin

django-unfold admin is the organizer ops surface for M1. A dedicated organizer UI gets built
only where the admin is demonstrably the wrong tool: video/talk matching is the likely first
case, since it wants a purpose-built review screen rather than a changelist.

### Authentication

Two audiences, one mechanism to start:

- Email magic links for everyone who logs in, speakers included. `accounts.User` with email as
  `USERNAME_FIELD` and no usable password.
- Django superusers keep working via `createsuperuser` for admin access.
- Authorization is `OrganizationMembership` with a role, so organizer permissions are
  org-scoped from day one rather than global `is_staff`.

Deferred: GitHub org/team OAuth, the legacy project's organizer auth. It is additive (another
way to authenticate an existing `User`) and it is PyOhio-specific, which is exactly the kind
of assumption this project is trying not to bake in. Revisit during M2, when the legacy
project's GitHub-authenticated users need a migration path.

### Email: Anymail, with provider and sender both configurable

Speaker magic links and review invitations are transactional mail, where deliverability is the
whole game. The PyOhio-hosted instance sends through **Postmark** from **confdash.org**.

The provider is configuration rather than a dependency, via **django-anymail**: `EMAIL_PROVIDER`
names any Anymail backend and `EMAIL_API_KEY` carries its credential. Leaving `EMAIL_PROVIDER`
blank falls back to `EMAIL_URL`, which is how development reaches mailpit. Swapping providers is
an env change.

Sender identity resolves at two levels, because both cases are real:

- **Deployment level** (`DEFAULT_FROM_EMAIL`): an organization self-hosting confdash has its own
  domain and provider, and should need no per-organization setup at all.
- **Organization level** (`Organization.from_email` / `from_name`): one instance hosting several
  organizations can give each its own sender, so PyOhio's mail does not appear to come from
  another conference. `Organization.sender_address()` resolves org-first, deployment-second.

These are real columns rather than `settings` keys because they need email validation and are read
on every send, which is the documented threshold for a JSONField value graduating to a column.

Operator caveat that no code can enforce: a custom sender domain must be verified with the
provider (SPF/DKIM) or mail is rejected. Adding an organization with its own domain is therefore
an operator step, not pure self-service.

### Conventions taken from the documented standard

Audited against `~/checkouts/metarepo/tmp/django-patterns/10-outline.md` after the initial
bootstrap. Adopted from there and worth not re-litigating:

- `common/` holds plain Python and abstract models and is **not** in `INSTALLED_APPS`. The rule is
  "Django app → its own app; plain module → `common/`".
- `settings.TESTING = "pytest" in sys.modules` as the test sentinel, rather than a test settings
  module.
- `User.data` as a `JSONField` escape hatch for preferences, so adding one needs no migration.
- `gunicorn.conf.py` with 2 workers x 4 threads and `forwarded_allow_ips`, not inline CMD flags.
  The workload is I/O-bound, so threads beat processes on memory.
- `STOPSIGNAL SIGINT`, because gunicorn treats SIGINT as the graceful shutdown.
- CI skips the container build on `bump:` commits, since Release builds the tag.
- `django_typer` belongs in `INSTALLED_APPS`, not merely in the dependency list.

## Deferred

### Deployment target

Open pending a cost comparison. Two candidates:

- **Reuse the legacy PyOhio infrastructure.** Heavier than it first sounds: a DigitalOcean
  droplet provisioned by Pulumi, configured by Ansible, secrets in Doppler under
  `pyohio/confdash`. The playbooks and the SSL story already exist, and the droplet may be
  reusable directly.
- **Fly.io.** Fewer steps to a first working URL with TLS and managed Postgres, but it means
  PyOhio running things in two places.

The container build in CI produces an image either way, so this blocks only the first real deploy.
M1.1 through M1.3 need none of it.

It is on the critical path from M1.4 onward: a magic link has to point at a real hostname. Since
PyOhio 2026 has already happened and its speakers are waiting, this is now a near-term decision
rather than an end-of-M1 one.

Outbound email is settled separately and is no longer coupled to this choice: Postmark via
Anymail works from either target.

### YouTube OAuth consent flow

The credential model supports it, but M1 seeds the YouTube connection with a refresh token
obtained out of band. A self-service "Connect YouTube" flow needs a Google app verification
review for sensitive scopes, which is a project of its own and is not on the M1 critical path.

### Background job runner

django-tasks (Django 6 native) when something needs it. Caption uploads and playlist syncs are
the likely first candidates, since both are slow enough to want off the request path. M1 can
start with management commands run by hand.

### API surface

django-ninja is the choice when there is an API, but M1 is server-rendered HTMX and needs no
public API. The legacy project's public sponsor/registration API is M2 scope.

When it arrives: routers per concern, and a separate `NinjaAPI` instance per audience rather than
one API with mixed auth. The legacy project has both an authenticated dashboard API and a public
sponsor API for the website to consume, which is exactly that split.

### Caching and queueing (Valkey)

Nothing needs a cache or a broker yet, so the dev stack is Postgres only. Valkey when something
does — not Redis, which is the same protocol under a license we would rather avoid. The legacy
project's dashboard caches expensive aggregate views, so the M2 port is the likely first need.

### A `specs/` tier

`plans/` (forward-looking) and `CLAUDE.md` (conventions) are enough at this size. The
`specs/` tier — normative descriptions of current behavior, updated in the same PR as the
behavior — earns its keep on larger, multi-person codebases. Revisit if this grows into one.
