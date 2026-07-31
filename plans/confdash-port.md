# M2: confdash port

Bring the legacy project's features into this one, then sunset `../confdash`.

Not broken into shippable slices yet, on purpose. The provider abstraction has met exactly one
`talk_source` and one `video_host` when M1 lands; porting Tito is the first time `ticketing`
gets a real implementation, and the abstraction will probably need adjusting. Plan the slices
after M1.

## Legacy inventory

Taken from `../confdash/src` at the time of bootstrap. Grouped by what porting each actually
means, not by app.

### Ticketing and registration (`registration/`)

The core of the legacy project. Becomes the `ticketing` capability plus a `registration` app.

| Legacy model | Port as |
| --- | --- |
| `Event` (keyed `account_slug` + `event_slug`) | already exists as `events.Event`; Tito slugs move into the binding config |
| `TicketRelease` | `registration.TicketType`, event-scoped, `external_id` |
| `Question` | `registration.Question`, event-scoped, `external_id` |
| `Registration` | `registration.Registration` |
| `Donation`, `Donor` | `registration.Donation`, `registration.Donor` |
| `Answer` | `registration.Answer`, keeps the registration-XOR-donation constraint |
| `ProcessedLocation`, `PostalCodeRemapping` | `registration.` analysis models, unchanged shape |
| `SystemEvent` | superseded by `integrations.SyncRun` |

`TitoTicketing` adapter from `registration/tito_client.py` and `services/tito_sync.py`. The
legacy sync auto-detects donation vs registration tickets and handles pagination; both behaviors
need to survive the port, with tests.

Also here: PSF donation sync (`sync_psf_donations`), which is a second, non-Tito source of
donations. Worth noting because it means `ticketing` is not the only thing that writes
`Donation` — the capability abstraction should not assume one writer.

### Analysis and reporting

- Goal analysis (`analyze_goals`, tfidf analyzer) over survey answers.
- Postal code processing and geocoding (`process_postal_codes`).
- Event statistics (`event_stats`).
- Year-over-year comparison. Currently a single `yoy_dashboard` view; benefits directly from
  `Event.series` rather than the legacy project's implicit "all events are PyOhio" assumption.

### Sponsorship (`sponsorship/`)

`SponsorTier`, `Sponsor`, `SponsorAsset`, `SponsorJobListing`, `AddOnSponsorship`, plus a public
API for the website to consume. `SponsorTier` is currently a wide table of boolean benefits
(`has_booth`, `has_program_ad`, ...); the legacy project already has
`plans/sponsorships-simplification.md` open against this. Port the simplification, not the
current shape.

Tiers are org-level, not event-level — an org's tier structure persists across years — which is
a genuine improvement the tenancy model enables.

### Dashboard (`dashboard/`)

No models, all views and templates: per-event dashboards for registrations, donations, survey
responses, geography, CFP, emails, t-shirts, sponsorships, plus year-over-year and a status
page. Caching helpers (`cache_utils`, `prewarm_cache`, `clear_cache`) exist because some views
are expensive.

The CFP dashboard is the interesting one: `../confdash/plans/cfp-dashboard.md` lists its data
source as an open question. This project answers it — `talk_source` sync from M1.1 already
populates `Talk` and `Speaker`, so the CFP dashboard becomes a reporting layer over data that
is already there. That makes it cheap, and it should be an early M2 slice rather than a late
one.

### Auth (`github_auth/`, `api_auth/`)

- `GitHubUser` (an `AbstractUser` subclass) with GitHub org/team membership caching and
  middleware. This project uses `accounts.User` with magic links instead, so the port is a
  GitHub OAuth login option plus a migration path for existing GitHub-authenticated users, not
  a model port. Org/team membership maps onto `OrganizationMembership`.
- `APIToken` bearer authentication, read-only flag, usage tracking. Ports mostly as-is when
  there is an API to protect, alongside django-ninja.

### Operational scripts

`scripts/build_badge_list.py`, `scripts/build_tshirt_pickup_sheet.py`, and Slack posting
(`post_stats_slack`). Currently a mix of standalone scripts reading spreadsheets and management
commands. Port as django-typer commands reading from the database.

### Legacy plans worth carrying forward

Open items in `../confdash/plans/` that should not be lost in the sunset: advanced goal
analysis, Buttondown email analytics, CFP dashboard, Discord bot, enhanced admin features,
error tracking, sponsorships simplification. Triage these into this repo's `plans/` during M2
rather than porting the files wholesale.

## Migration of existing data

The legacy database is SQLite with real PyOhio history (2024 and 2025 events at least). It is
not throwaway.

Approach: a one-shot import command reading the legacy database directly, creating one
`Organization` ("pyohio") and one `Event` per legacy `Event`, with `series="pyohio"`. Legacy
UUIDv4 PKs are not preserved — new UUIDv7 PKs are assigned and legacy IDs kept in an
`external_id` or `raw` field where traceability matters.

Decide during M2 planning whether historical registration data is worth importing at all, or
whether the legacy project stays readable in an archived state and this project starts fresh
from 2026. Importing is real work and the year-over-year dashboard is the only feature that
needs the history.

## Sunset

`../confdash` is archived once M2 is done and the last feature anyone actually uses has landed
here. Until then it stays running; nothing in this repo depends on it at runtime.
