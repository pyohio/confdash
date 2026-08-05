# Data model

The schema is designed for many organizations, each running many events across many years,
from the start. Rationale for the shape is in [decisions.md](decisions.md).

Conventions applied throughout:

- UUIDv7 primary keys on a hand-rolled abstract base (`common.models.BaseModel`) with
  `created_at` / `updated_at`. UUIDv7 sorts by creation time, so it indexes better than v4 and
  removes the need for a separate ordering column on append-heavy tables.
- Internal PKs are never external identifiers. Provider identifiers (`pretalx_code`,
  `youtube_video_id`) are separate fields, unique per event rather than globally.
- Everything below `Event` carries an `event` FK. Scoping is enforced in application code and
  query managers, not row-level security.
- `JSONField` for structured-but-variable data: provider config, provider payload snapshots.

## Tenancy

```
Organization
  slug            unique
  name
  settings        JSONField

OrganizationMembership
  organization    FK -> Organization
  user            FK -> accounts.User
  role            owner | organizer | viewer
  unique (organization, user)

Event
  organization    FK -> Organization
  slug                              # "2026"
  series          blank             # "pyohio", groups iterations for year-over-year
  name                              # "PyOhio 2026"
  start_date / end_date  nullable
  timezone                          # event-local time, for scheduling and deadlines
  is_active
  settings        JSONField
  unique (organization, slug)
```

`Organization.settings` and `Event.settings` hold policy that does not deserve a column yet.
Settings resolve event-first, falling back to org, so an org sets a default and an event
overrides it. The publication policy in [video-review.md](video-review.md) is the first real
consumer.

`role` is a plain choices field. It is deliberately coarse: three roles cover the actual
organizer/viewer distinction, and a permission matrix can come later if it is ever needed.

## Accounts

```
accounts.User  (AbstractBaseUser + PermissionsMixin)
  email           unique, USERNAME_FIELD
  name            blank             # single field, not first/last
  is_active / is_staff / is_superuser
  # no usable password by default; createsuperuser still sets one
```

A single `name` field rather than `first_name`/`last_name`: speaker names come from provider
data as one string, and splitting them is lossy for a good fraction of real names.

Users are global, not org-scoped. The same person organizing two conferences is one account
with two memberships. A speaker is a `User` too, reached by magic link, with no membership.

## Provider connections

The abstraction that keeps Pretalx, Tito, and YouTube out of the domain model. Architecture and
adapter contracts are in [provider-integrations.md](provider-integrations.md).

```
ProviderConnection
  organization    FK -> Organization
  slug                              # "pretalx-pyohio-2026"
  name                              # "PyOhio Pretalx (2026)"
  capability      talk_source | ticketing | video_host
  provider                          # "pretalx", "tito", "youtube"
  config          JSONField         # non-secret connection settings (api_base_url, ...)
  credentials     encrypted         # provider-shaped secret payload
  is_active
  last_verified_at  nullable        # last successful credential check
  unique (organization, slug)

EventProviderBinding
  event           FK -> Event
  capability      talk_source | ticketing | video_host
  connection      FK -> ProviderConnection
  config          JSONField         # event-specific: {"event_id": "pyohio-2026"}
  unique (event, capability)
```

Two levels because credentials and event-specific settings have different lifetimes.
Credentials are onboarding state; which Pretalx event to pull is per-iteration state. Pretalx
issues credentials per event, so an org holds several `pretalx-*` connections at once — hence
uniqueness on `(organization, slug)` and not `(organization, provider)`.

`unique (event, capability)` means one talk source per event. Multiple sources for one event is
a real thing eventually (a main CFP plus separately-managed keynotes) but it complicates every
sync path, so it is out of scope until something needs it.

Validation that `connection.capability == binding.capability` and that
`connection.organization == event.organization` lives in `clean()` and in the binding's admin
form; there is no DB constraint that can express the cross-table organization check without
denormalizing.

## Program

Local mirror of talk and speaker data, populated by the `talk_source` adapter.

```
Speaker
  event           FK -> Event
  user            FK -> accounts.User, nullable   # set when they first log in
  external_id                       # provider's speaker code, e.g. Pretalx "TXY7EW"
  name
  email
  biography       blank
  avatar_url      blank
  unique (event, external_id)

Talk
  event           FK -> Event
  external_id                       # provider's submission code
  title
  abstract / description  blank
  duration_minutes  nullable
  session_type    blank             # "Talk", "Keynote", "Tutorial"
  state           blank             # provider's state, e.g. "confirmed"
  scheduled_start / scheduled_end   nullable
  do_not_record   bool              # publication guard; never release regardless of review state
  unique (event, external_id)

TalkSpeaker
  talk            FK -> Talk
  speaker         FK -> Speaker
  unique (talk, speaker)
```

`Speaker` is event-scoped, so the same human speaking in 2026 and 2027 is two rows. That is
intentional: provider speaker codes are event-scoped, names and bios change between years, and
a cross-year identity table adds merge problems for no M1 benefit. `Speaker.user` is the
cross-year identity when one is needed, resolved by email at first login.

**No `raw` payload snapshot.** These models hold only the fields the app maps, which reverses the
original design of keeping the provider payload for diagnosability and backfill. A real Pretalx
submission ships reviewer scores, review comments, organizer notes, custom-question answers, and an
`invitation_token` that can claim the submission; none of that belongs on rows a speaker reaches to
review their own video. Sync is idempotent, so re-running it backfills a newly mapped field, and
`SyncRun` plus logging cover diagnosis.

Storing CFP review data is not ruled out, it just does not live here. A CFP surface for the program
committee is a real future feature that wants exactly that data, in its own models behind
`Scope.PROGRAM`. The sensitivity boundary is a model boundary enforced by scope, not a field filter
on a row two audiences share.

`do_not_record` is the one field mapped for a reason beyond display: a talk marked it must never be
published, whatever `review_state` says. See M1.7's guard.

## Video review

Detailed workflow in [video-review.md](video-review.md).

```
Video
  event           FK -> Event
  talk            FK -> Talk, nullable            # null until matched
  external_id                       # provider's video id, e.g. YouTube "dQw4w9WgXcQ"
  title                             # provider-side title, as uploaded
  description                       # provider-side description, as uploaded
  privacy_status                    # provider-reported: private | unlisted | public
  duration_seconds  nullable
  published_at    nullable
  matched_by      FK -> accounts.User, nullable    # who confirmed the talk link
  matched_at      nullable
  unmatchable     bool              # deliberately has no talk, e.g. a welcome or closing remarks
  review_state    pending | invited | changes_requested | approved
  approved_at     nullable
  approved_by     FK -> accounts.User, nullable
  publication_state  unpublished | scheduled | published
  unique (event, external_id)

CaptionTrack
  video           FK -> Video
  external_id     blank             # provider caption track id
  language                          # BCP 47
  is_draft
  source          provider | speaker_edit
  content         TextField         # SRT/VTT body
  content_hash                      # dedupe repeated syncs
  created_by      FK -> accounts.User, nullable
  # append-only: each edit is a new row, latest per (video, language) wins

ReviewInvitation
  video           FK -> Video
  speaker         FK -> Speaker
  token_hash      unique            # magic-link token, hashed at rest
  sent_at / expires_at / accepted_at   nullable

ReviewComment
  video           FK -> Video
  author          FK -> accounts.User
  body            TextField
  timecode_seconds  nullable        # optional pointer into the video
  resolved_at     nullable
```

Two separate state fields on `Video` because approval and publication are genuinely
independent: a speaker approving does not necessarily publish, depending on the event's
release policy. Collapsing them into one enum produces states like `approved_but_held` that
are really a pair of facts.

`publication_state` and `privacy_status` both describe **confirmed** provider state, never intent.
`scheduled` means a write is queued; `published` is set only after the provider confirms. Intent lives
in `ProviderWrite`, so there is no code path that can record a publication that did not happen. See
[provider-writes.md](provider-writes.md).

`talk` and `unmatchable` together give three matching states, which one nullable FK cannot express:
no talk and not unmatchable means not yet reviewed, no talk and unmatchable means deliberately
standalone (a welcome, closing remarks), and a talk set means matched. Without the flag, an organizer
cannot tell "nobody has looked at this yet" from "there is correctly nothing to link".

`title` and `description` mirror what was uploaded, which matters because the videography team writes
them and M1.3a may rewrite them. Keeping the as-uploaded values makes a normalization dry run
possible and gives something to revert to.

`ReviewInvitation.token_hash` is slated for removal: invitations become lifecycle state and the
emailed link carries an `accounts.LoginToken` instead. See [authentication.md](authentication.md).

`CaptionTrack` is append-only rather than mutable so a speaker's caption edit never destroys
the machine-generated original, and a bad edit can be rolled back by pointing at an earlier
row. `content_hash` makes re-syncing idempotent.

`review_state` on `Video` rather than on a join table: the review is of a video, and one video
has one review lifecycle even when a talk has several speakers. `ReviewInvitation` is per
speaker, so co-speakers each get their own link, and any of them can approve.

## Open questions

- Co-speaker approval: does one speaker approving suffice, or is consent needed from all? Cheap
  either way today; affects whether approval lives on `Video` alone. Currently modeled as
  any-speaker-approves.
- Caption formats: store as-fetched (YouTube serves SRT/VTT/sbv) or normalize to one format on
  ingest? Normalizing makes an editor simpler but risks lossy round-trips.
- Whether `ReviewComment` is needed in M1 at all, or whether speaker-reported problems are
  better handled as email to organizers. Modeled here because "check for problems" implies a
  place to record them.
