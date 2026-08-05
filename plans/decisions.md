# Bootstrap decisions

Decisions made at project start, with rationale. Things that are settled and unlikely to be
revisited should eventually graduate to `CLAUDE.md` or `docs/`; this file is the record of
*why*, including for the options not taken.

The living record of stack, layout, and quality-gate conventions is [CLAUDE.md](../CLAUDE.md);
this file explains the choices behind them. Both derive from the personal Django standard
documented outside this repo at `~/checkouts/metarepo/tmp/django-patterns/`.

## Resolved

### Naming

The project and its distribution name are `confdash`; the Django settings package is `project/`,
per the going-forward standard ("Django project metadata only, not a place for application
logic"). The working directory is `cd1` and the legacy project stays at `../confdash` for now; it
gets sunset once the port in M2 lands. Nothing in this repo references the legacy project at
runtime.

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

### Map only the fields the app uses; no provider payload snapshots

Superseded the original design, which gave `Talk`, `Speaker`, and `Video` a `raw` JSONField holding
the provider payload, for diagnosability and for backfilling a later field addition without
re-fetching.

Reversed once a live `pyohio-2026` response showed what a Pretalx submission actually contains:
populated `mean_score`, `median_score`, `reviews`, `answers`, `review_code`, and `invitation_token`,
the last of which is a credential that can claim the submission. Storing that would put a live
provider credential and confidential CFP review data on rows speakers reach to review their own
videos.

The neutral records in `providers/base.py` are therefore dataclasses with a fixed field set and no
catch-all, so the guarantee is structural rather than a filter an adapter could forget. A provider
adding a field changes nothing until someone deliberately maps it.

Rejected first: an allow-list filter on the payload before storing it. It works, but it mitigates a
problem there is no reason to have, and it leaves the door open for the next field to be admitted by
accident.

What the reversal gives up is small: sync is idempotent, so a re-run backfills a newly mapped field,
and `SyncRun` plus logging cover diagnosis.

This does not rule out storing CFP review data. A CFP surface for the program committee is a real
future feature that needs it, and it gets its own models gated behind `Scope.PROGRAM`. The
sensitivity boundary is a model boundary enforced by scope, not a field filter on a row shared by
organizers and speakers.

### Synced data is mirrored locally, not read through

`Talk`, `Speaker`, and `Video` are local canonical models populated by sync, keyed on internal
UUIDv7 PKs with provider identifiers stored as separate external-reference fields. Review
state attaches to local rows, the app stays usable when a provider is down or an org's trial
ends, and a provider swap does not orphan review history.

### Django runs in the compose stack, not on the host

There is one way to run the app in development, and it is the container. `just` recipes exec into
`app`, so nothing changes in daily use, but the host is no longer a supported place to run Django.

The dev stack should therefore publish only what a browser or a host tool needs: the app, mailpit's
web UI, and postgres for GUI clients. Mailpit's SMTP port is deliberately `expose`d rather than
published, since `app` is the only sender and it resolves mailpit by service name.

Consequences worth knowing:

- `DATABASE_URL` and `EMAIL_URL` are set in `docker-compose.yml` under `environment`, which takes
  precedence over `env_file`. A value in a developer's `.env` cannot point the stack at another
  database or redirect its mail.
- `just up -d` is a prerequisite for `manage`, `shell`, and the `test*` recipes. With only postgres
  up they fail with `service "app" is not running`. Only `lint` and `format` run without the stack.

Rejected: keeping a host-usable path in parallel. It meant two configurations to keep working and
two sets of comments explaining which values applied where, for a path nobody was using.

### Line length 120

Going-forward standard. Legacy confdash uses 88; no attempt is made to stay consistent with it.

### Organizer surface is the admin

django-unfold admin is the organizer ops surface for M1. A dedicated organizer UI gets built
only where the admin is demonstrably the wrong tool: video/talk matching is the likely first
case, since it wants a purpose-built review screen rather than a changelist.

### Organizer URLs are path-scoped under `/o/`

`/o/<organization_slug>/<event_slug>/videos/`, not a flat `/videos/<uuid>/`.

Path-scoped because it makes the tenant a routing concern, so `events.decorators.organizer_view` can
refuse before a view body runs. Deriving the tenant from the object being acted on means authorization
can only happen after a fetch, so every view has to remember to check, and a forgotten check looks
exactly like a working view. Both slugs are matched as a pair: `2026` exists in every organization, so
resolving an event by its own slug would serve another tenant's event to anyone who guessed one.

The `/o/` prefix rather than the organization slug at the root. Root-level slugs would need a reserved
word list to avoid colliding with `admin/`, `healthz/`, and static, and the prefix leaves room for
sibling namespaces (`/u/` and others) without retrofitting one later.

Speaker URLs stay flat and opaque: `/review/<uuid>/`, reached from an emailed link. Different problem,
different answer. A speaker arrives once and should not have to learn an organization slug, and a URL
carrying no org or event is nothing to enumerate. UUIDv7 primary keys already exist for this.

Subdomains per organization were rejected: wildcard DNS, a wildcard certificate, and cookie scoping,
to buy nothing the path does not.

### Authentication: two audiences, two mechanisms

Superseded an earlier plan to use magic links for everyone. Organizers and speakers have
different trust models, so they authenticate differently over one `accounts.User` model:

- **Organizers** authenticate against their organization's identity provider, so access derives
  from membership the org already administers (a GitHub org, an OIDC directory). Revoking there
  revokes here.
- **Speakers** use emailed magic links. No account setup for a one-time reviewer.
- **Operators** keep password login via `createsuperuser`, so admin access never depends on
  outbound email or a reachable IdP.

Authorization is `OrganizationMembership` plus granted scopes, org-scoped from day one rather
than global `is_staff`.

The **Django admin is for deployment operators only**. Organizers, PyOhio's included, get a
purpose-built organizer interface. The admin has no tenancy of its own, so any staff user would
otherwise see every organization's rows; restricting it is what makes "cross-organization access
is impossible" enforceable without a `get_queryset` override on every ModelAdmin.

Rejected: magic links as the single mechanism for both audiences. It leaves
`OrganizationMembership` as a second, unrevoked path around the IdP, so someone removed from the
GitHub org keeps organizer access.

No longer deferred: federated organizer login was previously postponed to M2 as PyOhio-specific.
Supporting any organization's identity provider, rather than only PyOhio's GitHub org, removes that
objection. Note that **GitHub is OAuth2, not OIDC** (it issues no ID tokens), so a single OIDC
client does not cover both.

Still open: which library. django-allauth, python-social-auth, and authlib are all live candidates,
judged mainly on whether they support multiple instances of one provider type (one OIDC issuer per
organization) and whether client secrets can come from the encrypted store. To be settled by a
spike when the organizer interface is built, not now. See
[authentication.md](authentication.md#library-evaluation).

Details, including the scope model and the speaker token refinements, in
[authentication.md](authentication.md).

### Email: Anymail, provider configurable, sender per deployment

Speaker magic links and review invitations are transactional mail, where deliverability is the
whole game. The PyOhio-hosted instance sends through **Mailgun** from **confdash.org**, chosen on
pricing.

The provider is configuration rather than a dependency, via **django-anymail**: `EMAIL_PROVIDER`
names any Anymail backend and `EMAIL_API_KEY` carries its credential. Leaving `EMAIL_PROVIDER`
blank falls back to `EMAIL_URL`, which is how development reaches mailpit. Swapping providers is an
env change, so the pricing comparison stays revisitable.

Two Mailgun-specific knobs, both optional: `EMAIL_SENDER_DOMAIN` (Mailgun addresses its send API
per domain; unset, Anymail derives it from the From address, which is right when
`DEFAULT_FROM_EMAIL` is already on the Mailgun domain) and `EMAIL_API_URL` (its EU region is a
different host).

**Sender identity is per deployment, not per organization.** One multi-org instance sends
everything as one address on confdash.org. An organization wanting its own domain runs its own
instance with its own provider, which is a deployment concern rather than a data-model one.

Rejected: `Organization.from_email` / `from_name` columns with an org-first fallback. Built briefly,
then removed. It bought nothing the deployment boundary does not already give, and it carried a
real cost — every additional sender domain needs SPF/DKIM verification with the provider, so
"self-service" per-org senders would have been an operator step wearing a data-model disguise.

Operator caveat no code can enforce: the sending domain must be verified with the provider or mail
is rejected.

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

Outbound email is settled separately and is no longer coupled to this choice: Mailgun via Anymail
works from either target.

### YouTube OAuth: app audience, and the tenancy fork it creates

The credential model supports a self-service "Connect YouTube" flow, but M1 seeds the connection with
a refresh token obtained out of band. Nothing has been created in Google Cloud yet, so no consent
screen exists and no token expiry clock is running: this is a setup decision to make before creating
credentials, not a deadline.

The choice of **app audience** is the consequential part, because it is a property of the Google Cloud
project rather than of the organization connecting:

| Audience | Refresh tokens | `youtube.force-ssl` review | Who can connect |
| --- | --- | --- | --- |
| Internal | No expiry | Not required | Only the project's own Workspace users |
| External, In Production | No expiry | Required | Any Google account |
| External, Testing | Expire after 7 days | Not required | Test users, max 100 |

PyOhio has Google Workspace, so **Internal is the right setting for the PyOhio instance**: no 7-day
expiry, and no verification review despite `youtube.force-ssl` being a sensitive scope.

The fork: Internal apps are restricted to users inside the project's own Workspace organization. An
organization with only a personal Google account and a YouTube channel, no Workspace, **cannot connect
to a shared instance configured Internal**. Serving those organizations means either External plus a
verification review, or that organization self-hosting with its own Google Cloud project.

That is the same shape as the two other per-project ceilings already recorded, the YouTube quota and
the email sending domain, and it resolves the same way: a shared instance is convenient until a tenant
needs its own provider relationship, at which point self-hosting is the answer. No code changes with
the audience setting, so this is an operator decision rather than an architectural one.

Setup detail that is easy to get wrong: the PyOhio channel is a **Brand Account**, so the OAuth consent
flow presents a channel picker. Authorizing as the personal Google account yields credentials that work
and see the wrong channel. The Workspace user doing the authorizing needs owner or manager access to the
brand channel.

### Background job runner

Still deferred, and the reason has sharpened. Caption uploads looked like the first candidate, but the
actual requirement turned out to be **durable intent, not asynchronous execution**: a provider write
that fails must leave a record of what was supposed to happen, so local state never claims something
about YouTube that is not true. That is a data-model concern, and a `ProviderWrite` table plus a drain
command solves it completely. See [provider-writes.md](provider-writes.md).

Conflating the two is how projects acquire a broker they never needed. django-tasks (Django 6 native)
remains the choice if throughput ever justifies a runner, and the table does not have to change to get
there. Nothing in M1 comes close: a few dozen writes per event, a few times a year.

Postgres `SELECT ... FOR UPDATE SKIP LOCKED` covers the one genuine hazard of a table-as-queue, which
is two drains claiming the same row.

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
