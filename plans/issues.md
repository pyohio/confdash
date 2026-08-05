# Issues

> Smaller TODOs and problems that do not warrant their own plan file.

- **Collapse migrations before the first live deployment.** Early development migrates forward and
  accumulates small migrations rather than rewriting them, so a squash is wanted once the models settle.
  Do it exactly once, before anything real has been deployed, since after that a collapse means
  reconciling deployed databases against a rewritten history.
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
- **The 2026 playlist does not exist yet**, since the videography team is still processing. Until it
  does, test reads against the 2025 playlist, which is real data with real formatting variance, and
  test writes against a scratch playlist holding one throwaway video. Never point a write path at 2025's
  published videos: a privacy or metadata bug there is publicly visible and overwrites real content.
- **Confirm YouTube quota costs** against the current table during M1.2. Working figures: reads 1 unit,
  `captions.list` 50, `captions.download` 200, `captions.insert` 400, `captions.update` 450,
  `videos.update` 50, against 10,000 units a day. They imply fetching captions on demand rather than
  in bulk; see `video-review.md` risks.
- **Consider starting a YouTube API Compliance Audit** before onboarding a second organization onto a
  shared deployment. Quota is per Google Cloud project, so orgs on one instance share 10,000 units a
  day, which is about one event's caption ingest. Not needed for PyOhio alone. The audit is free but
  discretionary and slow, so it wants lead time rather than being started when quota runs out.
- **Set the YouTube app audience to Internal when creating the Google Cloud credentials.** PyOhio has
  Workspace, so Internal avoids both the 7-day refresh-token expiry that Testing imposes and the
  verification review that `youtube.force-ssl` would otherwise need. Nothing exists in Google Cloud
  yet, so no clock is running. Authorize as a Workspace user with owner or manager access to the
  **Brand Account** that owns the channel, since the consent flow offers a channel picker and choosing
  the personal account yields credentials that see the wrong channel. See `decisions.md` for the
  tenancy consequence: Internal means only PyOhio users can connect to this instance.
- **No write-back path for rotating credentials.** `ProviderConnection.get_credentials()` reads;
  nothing persists a refresh. OAuth needs it, since access tokens expire hourly and refresh tokens
  can rotate. Adapters must not touch the ORM, so the resolver should inject an
  `on_credentials_refreshed` callback that owns the write. Needed for M1.2.
- **Collapse the two magic-link credentials.** `LoginToken` and `ReviewInvitation.token_hash` are two
  mechanisms for one job. Make `ReviewInvitation` lifecycle state only and have its link carry a
  `LoginToken` with `next_url` set. Do this in M1.4, before both exist in code.
- **Magic links must be consumed by POST.** Mail security scanners prefetch URLs and burn single-use
  GET links before the recipient clicks. Needs an interstitial page in M1.4.
- **An anonymous request to an organizer URL gets 403, not a redirect to login.** Correct as a default
  (fail closed) but poor UX, and there is no organizer login URL to redirect to yet. Revisit when
  organizer SSO is built: anonymous should redirect, an authenticated-but-unauthorized session should
  still get 403, since a redirect there would loop.
- **No login path sets an `AuthMethod`, so no organizer URL is reachable by a real browser session.**
  The confirm queue works and is covered by tests through the real URL and template, but exercising it
  by hand needs a session minted by hand. This is the single thing blocking anyone from actually using
  the screen, and it is what organizer SSO fixes. Until then, a dev-only login shim is the alternative
  if hand-driving the UI is wanted sooner.
- **The Tailwind Play CDN cannot take a subresource-integrity hash**, because it is unversioned and
  compiles classes in the browser, so its bytes change without notice. Every other CDN asset in
  `base.html` is pinned with a `sha384` hash. The Play CDN is also explicitly not meant for production.
  Decide before launch: pin a compiled stylesheet, accept a small build step (against the "no JS build"
  convention), or accept the risk knowingly.
- **The queue screen has not been checked in a real browser.** The layout was verified structurally
  (computed styles, DaisyUI and Tailwind applied, htmx and Alpine loaded, no horizontal overflow) by
  serving the rendered HTML, but no screenshot was captured and no HTMX swap has been clicked by hand.
  Worth doing once a login path exists.
- **Add an `update_metadata` write operation** when M1.3a gives the `video_host` protocol a method for
  it. Left out deliberately: an operation with no handler is a row that fails at drain time. The plan
  is to combine it with the privacy change into one `videos.update` call, since they cost 50 units
  together or separately.
- **`QuotaExceeded.retry_after` has no producer yet.** The outbox honours it and falls back to a fixed
  delay without one; the YouTube adapter in M1.2 is what should set it to the next midnight Pacific.
- **Cron the drain** once there is a deployment target, so a write deferred to tomorrow's quota does not
  wait for someone to run the command. Nothing else needs unattended execution.
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
