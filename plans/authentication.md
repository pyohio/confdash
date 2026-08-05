# Authentication and authorization

Two audiences with different trust models, so two authentication mechanisms over one identity
model. This supersedes the Authentication section of [decisions.md](decisions.md), which had
magic links for everyone and federated login deferred.

| Audience | Authenticates via | Authorization source |
| --- | --- | --- |
| Organizers | Their organization's identity provider | `OrganizationMembership` + granted scopes |
| Speakers | Emailed magic link | Being a `Speaker` on a `Talk` |
| Deployment operators | Password, or an operator IdP | `is_staff` / `is_superuser` |

One `User` model for all three. The audiences differ in how they prove identity and what they may
reach, not in what they are: a PyOhio organizer who also gives a talk is one user wearing both
hats, which is common enough that splitting the model would be wrong.

## Organizer authentication: federated

Organizer access derives from membership in something the organization already administers. That
is the property worth preserving from the legacy project: removing someone from the GitHub org
removes their dashboard access, with no second system to remember.

### GitHub is OAuth2, not OIDC

GitHub does not implement OpenID Connect for user login and issues no ID tokens. The legacy
`github_auth` app is a plain authorization-code flow followed by `GET /user` against the REST API.
Generic OIDC providers (Okta, Entra, Keycloak, Google Workspace, Authentik) do issue ID tokens and
publish a discovery document.

Two consequences that hold whichever library is chosen. Identity from GitHub comes from a REST call
rather than a verifiable token, so it is only as trustworthy as the transport and the token
exchange. And GitHub org and team membership has no OIDC-claim equivalent, so it needs its own API
calls with the `read:org` scope, which makes the membership check provider-specific work regardless
of how much of the flow a library covers.

### Provider configuration is per organization

Each organization brings its own IdP and its own client credentials. Nothing is shared across
organizations, and adding an organization must not require a redeploy, which is the same constraint
that drove `ProviderConnection` for sync credentials.

Identity provider config is **never event-bound**. Identity is an organization-level concern and
does not change per iteration, so there is no `EventProviderBinding` involved.

### Membership is the authorization decision

Federated login answers "who are you". `OrganizationMembership` answers "what may you do". Login
yields an identity plus whatever groups the provider reports (GitHub teams, OIDC group claims), and
a local service maps that to a membership. That mapping is ours in every candidate library, so it is
the part worth designing carefully.

Two provisioning modes, both needed:

- **Mapped**: connection config maps a group to a role, and a matching group auto-provisions or
  updates the membership on login. This is what the legacy project did, with GitHub org membership
  granting access and the `Dashboard Admins` team granting more.
- **Pre-provisioned**: a membership must already exist or login is refused. Necessary for IdPs
  without usable group data, and the only option for bootstrapping.

Hard rules:

- Failing the membership check must not create a session. Authenticated but unauthorized is a
  refusal, not a logged-in user with an empty dashboard.
- Never key a federated identity on a username or email. Both change. Key on the provider's
  immutable subject: GitHub's numeric `id`, OIDC's `sub`.
- Re-check membership on a cadence, not only at first login. If access is cached in a local row and
  never revalidated, deprovisioning silently fails: someone removed from the GitHub org keeps
  working until their session expires. The legacy project cached team membership for an hour, which
  is the right shape.

### Organizer email may be undeliverable, and that is fine

Legacy fell back to `{login}@users.noreply.github.com` when a GitHub email was private. Since
organizers no longer need to receive mail to log in, this stops being an auth problem. It remains a
notification problem, so flag such accounts rather than letting notification code mail them.

## Speaker authentication: magic link

Unchanged in mechanism, with three refinements.

**One credential mechanism, not two.** `ReviewInvitation` should hold lifecycle state (sent, opened,
approved, stale) and stop being a credential. The emailed link carries a `LoginToken` whose
`next_url` deep-links to the review page, which is what that field exists for. Resending an
invitation mints a fresh token instead of reusing a long-lived one, and there is one code path to
rate-limit, expire, and test.

**Consume by POST, not GET.** Mail security scanners prefetch URLs, which burns a single-use GET
link before the human clicks it and produces an "invalid link" report with no bug behind it. The
link lands on an interstitial page and a POST consumes the token.

**Two lifetimes.** An invitation-borne link should last about a week, since speakers act on their
own schedule. A self-service "email me a link" request should last minutes. Same mechanism,
different expiry at mint time.

Unchanged: tokens hashed at rest, single-use, rate-limited, and no user-existence disclosure in the
request response.

## The two paths do not grant the same access

**Built.** `accounts/auth_method.py`.

A speaker magic link must not confer organizer access, even to a user who holds an
`OrganizationMembership`. Otherwise the membership row becomes a second, unrevoked path around the
IdP: someone removed from the GitHub org still magic-links into the organizer console.

So the session records how it was authenticated, and organizer-scoped views require an allowed
mechanism. An organizer who is also a speaker can magic-link in and review their own talk; reaching
the organizer console requires logging in through the IdP.

`AuthMethod` is federated, magic_link, or password. `permits_organizer_access()` allows federated
outright and password only for superusers, since operators are provisioned by hand and nobody
obtains a password by joining a GitHub org. An allow-list rather than a "not magic link" test, so a
mechanism added later has to be granted organizer access explicitly instead of inheriting it. A
session with no recorded mechanism is refused.

`set_auth_method()` must be called at every login, or that login grants nothing.

## Authorization: scopes from the start

**Built.** `events/scopes.py`, `events/authz.py`, and `OrganizationMembership.scopes`.

Access is all-or-nothing today, but the eventual shape is domain-scoped: program committee sees CFP
and videos, sponsorship sees sponsors and donations, comms sees the email list. The scope argument
exists now anyway, because retrofitting it across every call site later is the expensive part and
passing an always-satisfied scope costs nothing.

`Scope` is a StrEnum of program, videos, sponsorship, and comms. Granted scopes live in
`OrganizationMembership.scopes`, a JSONField list, so adding a member needs no migration.

Two rules worth knowing, both covered by tests:

- **Empty means unrestricted**, and is the default. Seeding every scope at creation would instead
  mean a newly added `Scope` member silently locks existing organizers out of a new area of the app.
  Restricting a membership is the deliberate act; unrestricted is the resting state.
- **Owners always hold every scope**, restriction notwithstanding, because an owner restricted out
  of an area could not restore their own access.

`clean()` rejects unknown scope names, since a typo is otherwise a silent authorization bug that
withholds access nobody meant to withhold. Unknown values already stored are ignored rather than
raising, so a name left behind by a downgrade fails closed instead of breaking every request.

Deliberately deferred: **event-scoped** scopes, for someone on 2026's program committee but not
2027's. A real eventual need, and an additive table when it arrives. Membership stays org-scoped.

Also deferred: read versus write within a scope. `Role.VIEWER` exists and `can_manage` distinguishes
it, but scope is a domain axis and read/write is a separate one. They should not be conflated into a
single list.

### One place decides organizer access

`events.authz` requires all three of an allowed auth method, a membership in *that* organization, and
the scope. Kept as one predicate so there is one thing to audit: `CLAUDE.md` requires that
cross-organization access be impossible, and since that cannot be a database constraint it has to be
a function every organizer path goes through.

`require_org_scope()` raises `PermissionDenied` without distinguishing "not a member" from "wrong
scope" from "wrong login method", so a response cannot tell an outsider whether an organization
exists or who belongs to it.

`events.decorators.organizer_view(scope)` applies those predicates to a request. Organizer URLs are
`/o/<organization_slug>/<event_slug>/...`, so the decorator resolves the slug pair, checks the scope, and
passes the view an `Event` rather than two strings:

```python
@organizer_view(Scope.VIDEOS)
def confirm_queue(request, event): ...
```

The tenant is in the path precisely so authorization happens before a view body runs; see
`decisions.md`. `require_scope` and `has_scope` cover the in-view cases: a second scope for an action on
an already-authorized page, and a non-raising check so a template does not render a button the organizer
cannot use.

An anonymous request currently gets 403 rather than a redirect, because there is no organizer login URL
to redirect to. That should change when SSO lands, for anonymous sessions only: redirecting an
authenticated-but-unauthorized session would loop.

## Speaker authorization

A speaker reaches only their own talks, in their own events. Nothing broader: not other talks in the
same event, not their own talks in an event they were not accepted to.

Not yet built, because `Talk`, `Speaker`, and `Video` arrive in M1.1 and M1.2. It belongs beside
`events.authz` when it does, scoped through `Speaker.user` rather than by fetching an object and
checking its owner afterwards, since the latter leaks existence through timing and error differences.

## The Django admin is for operators only

The unfold admin is for a small number of PyOhio people with `is_staff`. Organizers, PyOhio's
included, use the organizer interface instead.

- `is_staff` is granted deliberately and is **never derived from a group mapping**. This is a
  behavior change from legacy, which set `is_staff` from the `Dashboard Admins` team and pointed
  `LOGIN_REDIRECT_URL` at `/admin/`. The port must drop that mapping.
- Superusers keep password login, so operator access does not depend on outbound email or on an
  external IdP being reachable.
- This resolves a real gap: the admin has no tenancy, so any staff user sees every organization's
  rows including their `ProviderConnection` records. Restricting the admin to operators makes
  "cross-organization access is impossible" enforceable in the organizer interface, rather than
  requiring a `get_queryset` override on every ModelAdmin where one omission is a leak.
- Useful side effect of federated organizer login: 2FA policy belongs to the IdP. A GitHub org that
  requires 2FA gets it for confdash for free.

## Models

- `OrganizationMembership.scopes`: **built**, migration `events/0002`.
- A federated-identity record: user FK, provider, immutable subject, cached claims, last-verified
  timestamp, unique on `(provider, subject)`. Not built. Whether this is a model we write or one a
  library already provides depends on the choice below, so it is not specified here.

`LoginToken` needs no change. `User` needs no change.

## Library evaluation

**Open. Not decided, and deliberately not decided yet**: federated login lands with the organizer
interface rather than in M1, so this can be settled against a working spike instead of on paper.

The part that should not be hand-rolled is ID token validation: JWKS signature verification plus
issuer, audience, expiry, and nonce checks. Getting it wrong is an auth bypass, not a bug. The
authorization-code flows around it are ordinary.

### Criteria

1. Both protocol families: GitHub's OAuth2-plus-REST and standards-compliant OIDC.
2. **Multiple instances of the same type**, since each organization has its own OIDC issuer and
   client credentials. This is the requirement that eliminates most candidates.
3. Onboarding an organization must not need a redeploy, so provider config lives in the database.
4. A hook to run the org and group membership check and refuse login on failure.
5. Coexistence with the speaker magic-link path, without a second competing login system.
6. Credentials sourced from the encrypted store, or a recorded decision to treat OAuth client
   secrets as a lower risk class.

### Candidates

**django-allauth**, socialaccount only. Its `openid_connect` provider hosts multiple independent
sub-providers, each with its own `provider_id`, client ID, secret, and server URL, and it ships a
maintained `github` provider. Meets 1 through 3 directly. Open costs: it brings its own account,
email, and signup machinery that would need disabling to satisfy 5; membership checking is custom
either way, via a `pre_social_login` hook; and its `SocialApp` stores secrets as plain columns, so 6
needs resolving. The specific thing to verify in a spike is whether its adapter can source app
credentials at runtime from our own encrypted store rather than its table, which would settle 6
cleanly.

**python-social-auth**, via `social-app-django`. Comparable backend catalogue. Needs checking on 2
and on maintenance activity relative to allauth.

**authlib**, with the flows and all tenancy owned locally. Strongest on 2, 3, and 6, since nothing
constrains where credentials live, and GitHub becomes largely a port of the existing legacy service.
The cost is owning a provider adapter and its edge cases per provider type, indefinitely.

**mozilla-django-oidc**. Listed to be ruled out with a reason: single-issuer, settings-configured,
and no OAuth2-only providers, so it fails 1, 2, and 3.

### Leaning

The tradeoff is a maintained provider catalogue against tenancy and credential control. Since
supporting other organizations' IdPs is an explicit goal rather than a hypothetical, the catalogue
argument is stronger than it first looked, which is why the earlier draft of this document was wrong
to reject allauth outright. But the plaintext-secret question is a genuine conflict with a stated
security invariant, so it decides the matter and should be answered by a spike, not an assumption.

## Testing SSO locally

The dev stack should be able to exercise organizer SSO without a real IdP, the way outbound mail
already goes to mailpit rather than a real provider. Two shapes are possible:

- **A mock OIDC provider**, such as `ghcr.io/navikt/mock-oauth2-server`, as a single compose service
  publishing a discovery document. No bootstrap, and it mints tokens carrying whatever claims are
  asked of it.
- **A real self-hosted IdP** such as Keycloak or Zitadel, which genuinely models organizations,
  users, and roles, at the cost of several services and a bootstrap step.

**Decided: the mock provider.** The group mapping consumes group claims, and a mock server emits
arbitrary claims, so it can drive the mapping without the setup cost. A real IdP would earn its keep
only if real-IdP behaviors need validating (consent, refresh, session revocation), which is a later
question.

Two limitations to plan around:

- **The mock provider covers the generic-OIDC path only.** GitHub is the primary provider and cannot
  be mocked this way, since org and team membership are REST calls rather than claims. That path
  needs either a real OAuth app with a `localhost` callback plus a test org, or recorded fixtures for
  the membership calls. Worth deciding before the GitHub adapter is written, because it determines
  whether its tests are honest.
- **The issuer hostname has to match on both sides.** OIDC validates the `iss` claim, and the
  container performing the token exchange and the browser being redirected must reach the provider by
  the *same* name, or validation fails in a way that reads as a config error. Using
  `host.docker.internal` for both avoids it; `localhost` in the browser and a service name in the
  container does not.

Pin the provider image, for the same reason mailpit is pinned.

## Open questions

- **Login URL routing.** Reaching an org's IdP requires knowing which org, before anyone is
  authenticated. Now that organizer URLs are settled as `/o/<org>/<event>/...`, an org slug in the login
  path (`/o/<org>/login/`) is the obvious match, and the `/o/` prefix leaves room for it.
- **Bootstrapping a new organization.** The first membership cannot be group-mapped, since no
  membership exists to authorize the mapping. An operator creating the organization and its first
  owner in the Django admin is the likely answer and follows from the operators-only boundary.
- **Whether operators may use an IdP into the admin.** Convenient, with password as break-glass. No
  reason not to, but it is a separate decision from organizer login.
- **Whether OAuth client secrets must be encrypted at rest.** `CLAUDE.md` requires it of provider
  credentials. A client secret is arguably a lower risk class, since it is unusable without a
  matching registered redirect URI and a user completing consent, whereas a Pretalx API key is
  directly usable. If that distinction is accepted it needs recording as a decision rather than
  being breached quietly, because it is what makes some library options viable.

## Sequencing

Landed already, because both are cheap now and invasive to retrofit: the scope layer and the
auth-method distinction. Neither has a caller yet, which is the point of doing them first.

Next, in order:

1. **Speaker magic links (M1.4).** Gates M1.5 and M1.6, and PyOhio 2026's speakers are the waiting
   audience. Must call `set_auth_method(request, AuthMethod.MAGIC_LINK)`.
2. **Speaker authorization**, once M1.1 and M1.2 have created `Talk`, `Speaker`, and `Video`.
3. **Organizer SSO**, which is what makes the decorator reachable by a real session: nothing sets
   `AuthMethod.FEDERATED` yet, so only an operator password session passes it today.
4. **Federated organizer login**, with the organizer interface. Not on the M1 critical path, because
   M1's organizers are operators who already have admin access.

Until step 4, no login path sets `AuthMethod.FEDERATED`, so `events.authz` will refuse every
organizer request. That is correct rather than a gap: the organizer surface is the Django admin,
which does not go through `events.authz`. It does mean the first organizer view cannot ship before a
login path that sets the method.
