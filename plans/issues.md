# Issues

> Smaller TODOs and problems that do not warrant their own plan file.

- **Remove references to local checkout paths before launch.** They point at one maintainer's
  machine and mean nothing to anyone else working in this repo. Currently in `README.md` and
  `CLAUDE.md` (`../confdash`), `plans/confdash-port.md` (several), `plans/decisions.md`
  (`~/checkouts/metarepo/tmp/django-patterns/`, `../confdash`), and
  `plans/provider-integrations.md` (`../static-website/pyohio-cli/...`,
  `../confdash/src/registration/tito_client.py`). Each is either a reference that should name a
  public URL, a fact that should be restated in this repo so it survives on its own, or a note that
  should be dropped once the port lands. Do this when the production system launches, since the
  legacy paths are still genuinely useful during M2.
- **Credential key rotation.** `FIELD_ENCRYPTION_KEY` has no rotation path. Needs a
  `rotate_credentials` management command that re-encrypts every `ProviderConnection` under a
  new key, and a documented procedure. Not urgent until a second org is onboarded, but it is a
  real operational gap once the app holds tenant secrets.
- **Deployment target undecided**, pending a cost comparison of reusing the legacy DigitalOcean
  droplet versus Fly.io. Blocks M1.4 onward, since a magic link needs a real hostname. See
  `decisions.md`.
- **Verify confdash.org with Mailgun** (SPF/DKIM DNS records) before M1.4 sends anything. Until
  then mail from that domain will be rejected.
- **Get the 2026 playlist id** into the event's `video_host` binding config. The playlist is unlisted,
  so it has to be supplied rather than discovered.
- **Confirm YouTube quota costs** against the current table during M1.2. Working figures: reads 1 unit,
  `captions.list` 50, `captions.download` 200, `captions.insert` 400, `captions.update` 450,
  `videos.update` 50, against 10,000 units a day. They imply fetching captions on demand rather than
  in bulk; see `video-review.md` risks.
- **Consider starting a YouTube API Compliance Audit** before onboarding a second organization onto a
  shared deployment. Quota is per Google Cloud project, so orgs on one instance share 10,000 units a
  day, which is about one event's caption ingest. Not needed for PyOhio alone. The audit is free but
  discretionary and slow, so it wants lead time rather than being started when quota runs out.
- **YouTube OAuth consent screen must not stay in Testing status.** Google expires refresh tokens
  after 7 days for apps in Testing, so M1.2 would work for a week and then fail. Marking the app
  Internal avoids both the expiry and a verification review, but needs Google Workspace. Check this
  before building the adapter, and check whether the channel is a Brand Account, since consenting as
  the personal account yields credentials that authenticate but see no videos.
- **No write-back path for rotating credentials.** `ProviderConnection.get_credentials()` reads;
  nothing persists a refresh. OAuth needs it, since access tokens expire hourly and refresh tokens
  can rotate. Adapters must not touch the ORM, so the resolver should inject an
  `on_credentials_refreshed` callback that owns the write. Needed for M1.2.
- **Collapse the two magic-link credentials.** `LoginToken` and `ReviewInvitation.token_hash` are two
  mechanisms for one job. Make `ReviewInvitation` lifecycle state only and have its link carry a
  `LoginToken` with `next_url` set. Do this in M1.4, before both exist in code.
- **Magic links must be consumed by POST.** Mail security scanners prefetch URLs and burn single-use
  GET links before the recipient clicks. Needs an interstitial page in M1.4.
- **Organizer view decorator** still to write, wrapping `events.authz.require_org_scope`. Waiting on
  the URL shape, which M1.3 forces. The predicates it wraps are done.
- **Speaker authorization** to write once M1.1 and M1.2 create `Talk`, `Speaker`, and `Video`: a
  speaker reaches only their own talks in their own events, scoped through `Speaker.user`.
- **Drop the legacy GitHub-team-to-`is_staff` mapping in the M2 port.** Legacy set `is_staff` from
  the `Dashboard Admins` team and redirected logins to `/admin/`. Organizers no longer get admin
  access, so that mapping must not survive the port.
- **Pick a federated-auth library** (allauth, python-social-auth, authlib) via a spike when the
  organizer interface is built. Decided mainly by multi-instance provider support and whether client
  secrets can come from the encrypted store. See `authentication.md`.
- **Add a mock OIDC provider to the dev stack** when organizer SSO is built, so the flow is testable
  without a real IdP. Note it cannot cover GitHub, whose org and team membership are REST calls
  rather than claims. See `authentication.md`.
