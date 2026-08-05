# M1: Video review

The first feature. Organizers pull videos from a video host, match them to talks, and invite
speakers; speakers review their video and captions and approve publication.

Target: PyOhio 2026, using Pretalx as `talk_source` and YouTube as `video_host`.

Models are specified in [data-model.md](data-model.md); the provider layer in
[provider-integrations.md](provider-integrations.md).

## Definition of done

An organizer can take a YouTube playlist of unlisted PyOhio 2026 videos and end with every
video either published or explicitly held, with each speaker having approved their own, without
anyone touching a spreadsheet or the YouTube Studio UI.

## Slices

Each slice is independently shippable and leaves the system working.

### M1.1 Program sync

Pretalx adapter plus talk/speaker sync.

- `PretalxTalkSource` implementing `TalkSource`, ported from `pyohio-cli`.
- `sync_program` service: idempotent upsert of `Speaker`, `Talk`, `TalkSpeaker`.
- `just manage sync_program --event <slug>` (django-typer).
- Recorded-fixture tests for the adapter, fake-adapter tests for the service.

Done when PyOhio 2026's confirmed talks and speakers are in the database and re-running the
sync changes nothing.

### M1.2 Video sync

YouTube adapter plus video ingest.

**Uploading is not ours.** The videography team processes recordings and uploads them to the
organization's own YouTube channel, leaving each video unlisted and collecting them in a per-event
playlist that is also unlisted. This app reads that playlist; it never uploads. That is a hard
boundary and the reason `VideoHost` has no upload method.

Because the uploads live on the organization's channel, the connection's credentials can edit them,
which is what makes M1.5, M1.7, and M1.3a possible at all.

Unlisted rather than private matters: an unlisted video can be embedded and watched by anyone holding
the link, so a speaker can review theirs without a YouTube account or channel access. A private video
can only be viewed by the owner, which would make speaker review impossible.

- `YouTubeVideoHost` implementing `list_videos`, reading the playlist id from the binding config.
- OAuth credentials seeded out of band into a `ProviderConnection` (see decisions: the
  self-service consent flow is deferred).
- `sync_videos` service: upsert `Video` rows, unmatched, with `privacy_status` as reported.
- `just manage sync_videos --event <slug>`.

Read pattern: `playlistItems.list` for the video ids, then `videos.list` batched 50 ids at a time for
authoritative title, description, `privacyStatus`, and duration. The playlist item's own snippet can
go stale against the video, so it is not the source of truth.

Done when the playlist's videos are `Video` rows with `talk` still null.

### M1.3 Matching

The organizer step that turns unmatched videos into talk-linked ones.

**Videos carry the real talk titles, not placeholders**, because the videography team names them
from the schedule when uploading. Formatting differs though: separators, punctuation, case, and
truncation. So matching is title-based and should get most of the way there on its own, with human
confirmation rather than human data entry.

- Manual entry first: **built.** A video reference field, editable from the video admin or inline on a
  talk, with `videos.services` owning the match so the confirm queue and the admin share one
  implementation and neither can forget the audit stamps. This is the floor, it works before any OAuth
  setup, and it stays the fallback whenever the matcher is unsure. Everything below only makes it
  faster.

  Turning a pasted reference into a provider id is delegated to the video host's
  `parse_external_id`, since recognizing a watch URL is provider knowledge. With no host configured a
  bare id is accepted but a URL is refused: `external_id` is unique per event, so storing an
  uninterpreted URL would silently duplicate the video the first time a real sync reported it.
- Suggestion pass: **built** in `videos/matching.py`. Normalized fuzzy match producing ranked scored
  candidates. Deterministic, stdlib `difflib` only, no ML: there is nothing a model would learn that
  normalization does not already handle, and an unexplainable ranking is worse than a slightly weaker
  one when a human confirms every match anyway.

  Measured against the real 2025 corpus, 13 uploaded files against 39 confirmed talks: **10 of 10 real
  talks matched at 1.00 with a clear margin, 3 non-talks correctly produced no suggestion, no false
  matches.** The three misses are the welcome, the closing remarks, and a keynote recording, which is
  exactly the standalone case.

  Two things that tuning against real data taught, rather than intuition:

  - Comparing the **separator-collapsed** forms as well as the space-separated ones. Uploaders drop a
    hyphen (`Code-Scape` becoming `CodeScape`) as readily as they replace it, and treating every hyphen
    as a space scored the corpus's only real mismatch at 0.89 instead of 1.00.
  - **High confidence alone is not enough to auto-accept.** Two talks in a series with near-identical
    titles can both clear the threshold, so `is_unambiguous` also requires a margin over the runner-up.
    Without it, bulk-accept would make a coin toss look like a decision.
- Organizer review screen: **built**, at `/o/<org>/<event>/videos/`. Unmatched videos with ranked
  suggestions, each row its own HTMX swap target so confirming one leaves the rest of the queue and the
  scroll position alone. The score is shown, because an organizer wondering why a suggestion appeared
  deserves an answer.
- Bulk-accept high-confidence matches, with an undo: **built**. Accepts only the unambiguous ones and
  deliberately leaves near-ties in the queue. Undo works on either outcome and clears the audit stamps,
  since leaving them would claim someone decided the state the video is now in.

Expect videos that match no talk at all: the 2025 playlist included a welcome, closing remarks, and
a keynote recording alongside the talks. Marking a video **standalone** is a normal outcome, not an
error. Expect the reverse too, since not every talk gets a usable recording.

Standalone does not mean excluded. Those videos still want review and publication, they just have no
speaker to ask, so **staff review them instead**. `review_track` derives from the matching outcome, and
both tracks converge on the same `approved` state and the same publication path.

Done when every video for an event is either matched to a talk or marked standalone, and neither
outcome leaves a video that cannot be published.

Open: nothing blocking. The scorer can be tuned once a real playlist exists to test against.

### M1.3a Metadata normalization

Optional, and separable from matching. Videos arrive titled and described by whoever uploaded them;
publication is the moment to give them a consistent public form.

- A per-event title and description template, resolved through `Event.settings`, so each event can
  choose its own convention.
- `VideoHost` needs a metadata-write capability, which no protocol method covers yet. That is the
  one piece of provider surface this adds.
- Dry run that shows current versus proposed for every video before anything is written, since this
  overwrites work the videography team did.
- Idempotent: re-running against an already-normalized video changes nothing.
- Audit trail of what was rewritten and when, matching the publication action.

Sequence it with M1.7, since both are writes to the host and both want the same dry-run and audit
treatment. Not required for a speaker to review a video, so it must not gate M1.5.

### M1.4 Speaker authentication

Passwordless login, needed before anyone can be invited.

- Magic-link request and consume flow, tokens hashed at rest, single-use, expiring.
- `Speaker.user` resolution by email on first login.
- Authorization: a speaker may only see videos for talks they are a speaker on.
- Rate limiting on link requests, and no user-existence disclosure in the response.

Done when a speaker can click an emailed link and land on their own video, and cannot reach
anyone else's.

Email is settled: Anymail with Mailgun, sending from confdash.org for the whole deployment. Still
needs a deployment with a real hostname, since a magic link has to point somewhere, and DNS
verification of confdash.org with Mailgun.

### M1.5 Speaker review

The speaker-facing surface.

- Video player with the unlisted video embedded.
- Caption fetch on first need rather than in a bulk sync, cached in `CaptionTrack` and never
  re-downloaded. This is a quota decision, not a laziness one: see the risks below.
- Caption viewing, with a transcript view aligned to playback.
- Caption editing and re-upload: a new `CaptionTrack` row plus a queued `ProviderWrite`, committed in
  one transaction so a failed push cannot leave us believing captions were corrected when they were
  not. See [provider-writes.md](provider-writes.md).
- Report a problem: a comment, optionally timecoded.
- Approve, which sets `review_state` and stamps `approved_at` / `approved_by`.

Done when a speaker can view, correct captions, and approve, and the corrected captions are
live on the host.

Open: caption editor scope. A plain textarea over the raw SRT/VTT is hours of work and is
genuinely usable by a technical audience; a cue-by-cue editor with playback sync is a
significant build. Start with the former unless the audience argues otherwise.

### M1.5a Staff review of standalone videos

Standalone videos still want review and publication, they just have no speaker to ask. An organizer
holding `Scope.VIDEOS` reviews them instead.

Deliberately the **same surface** as M1.5 rather than a parallel one: player, caption view, caption
edit, approve. The only differences are who may reach it and that there is nobody to invite. Building
it twice would mean two caption editors and two approval paths to keep honest, which is how they drift.

- Reuses M1.5's review view, authorized by scope instead of by speaker identity.
- Approve stamps `review_state`, `approved_at`, and `approved_by` exactly as a speaker approval does,
  so M1.7 needs no special case.
- A button that enqueues publication, on the same `ProviderWrite` path M1.7 uses.

Numbered after M1.5 because it is that surface with different authorization. Cheap once M1.5 exists,
and near-free to get wrong-headed about if built first.

### M1.6 Invitations

- Compose and send review invitations per speaker, per video.
- Templated email, org/event branding from settings.
- Organizer view of invitation status: sent, opened, approved, stale.
- Resend and bulk-send.
- Never invite anyone for a standalone video: there is no speaker, and M1.5a covers it. A bulk-send
  that quietly skips them is the correct behavior, not an omission.

Done when an organizer can invite every matched talk's speakers in one action and see who has
not responded.

### M1.7 Publication

- Per-event release policy in `Event.settings`, resolving to org default:
  `immediate` (flip to public on approval) or `hold` (approve now, release together).
- Immediate: on approval, queue a privacy write; `publication_state` becomes `published` only once the
  provider confirms it, never on the strength of having asked.
- Hold: a playlist-wide release action that queues writes for all approved videos, with a dry run.
- Guards, checked when the write executes rather than when it is queued, since approval can be
  withdrawn and `do_not_record` can be set in between: never publish a video that is not `approved`,
  and never publish a talk marked `do_not_record`.
- Audit: who released what, when. The `ProviderWrite` row already carries `requested_by` and
  `confirmed_at`, so this is a view over existing data rather than a second log.

The write machinery this depends on is built ahead of schedule, because retrofitting "record intent
before acting" means revisiting every call site that already acted. In place: the queue, the
`set_privacy` and `upload_captions` handlers with their guards, admin actions that queue publication and
retraction, and `just manage drain_provider_writes`. What M1.7 adds is the release *policy* and the
bulk path, not the mechanism.

Done when both policies work end to end against YouTube, both guards are covered by tests, and a
publish interrupted by quota exhaustion resumes on the next drain without anything being
double-published or silently skipped.

## Sequencing

M1.1 and M1.2 are independent and can go in either order. M1.3 needs both. M1.4 gates M1.5 and M1.6.
M1.5a follows M1.5, being the same surface under different authorization. M1.7 is last because it is the
only irreversible step: a video made public cannot be un-published without someone noticing.

M1.3a is optional and sequences with M1.7, since both are provider writes wanting the same dry-run and
audit treatment.

One useful property of M1.5a: standalone videos need no magic links, so **staff review does not depend
on M1.4 or on a deployed hostname**. If the deployment decision slips, the welcome and closing-remarks
videos can still be reviewed and queued for publication while speaker review waits.

## Risks

- **YouTube API quota, and it is captions that cost.** The default allocation is 10,000 units a day.
  Reading is effectively free: `playlistItems.list` and `videos.list` are 1 unit each, so ingesting a
  whole playlist costs about 2 units. Captions are the opposite: `list` is 50, `download` 200,
  `insert` 400, `update` 450, and `videos.update` is 50.

  For roughly 31 videos, downloading every caption track once is about 7,750 units, or 78% of a day,
  before a single publish. Three consequences, all design decisions rather than warnings:

  - **Fetch captions per video, on demand** (first speaker visit, or on invitation), never as a bulk
    prefetch. Cost then spreads across days as speakers engage instead of arriving as one spike.
  - **Download each track once and keep it.** `CaptionTrack` is append-only with a `content_hash`, so
    re-downloading is never necessary.
  - **Combine the privacy flip and any metadata rewrite into one `videos.update`.** Both touch the
    same resource with `part=snippet,status`, so doing them separately doubles the write cost for
    nothing.

  Confirm these figures against the current quota table when M1.2 lands, since they drive the
  sequencing above.

  **The quota is a hard limit with no paid overage.** There is no billing dimension for it: unlike
  most Google Cloud APIs, YouTube Data API quota cannot be bought. On exhaustion every call returns
  `403 quotaExceeded` and stays that way until reset, which happens at **midnight Pacific Time** on a
  fixed daily boundary rather than a rolling window. Exhausting it at 09:00 PT means no video
  operations for fifteen hours. Every request costs at least one unit including invalid ones, so a
  retry loop on errors burns quota; retries need a ceiling.

  More quota is granted, not purchased: it requires passing a **YouTube API Compliance Audit** via
  the Audit and Quota Extension Form. Free, but discretionary and slow, so the app must be designed to
  live inside 10,000 units a day and treat any extension as a bonus.

  **Multi-tenancy consequence.** Quota is scoped to the Google Cloud project that owns the OAuth
  client, not to a channel or a user. One deployment serving several organizations therefore shares a
  single 10,000-unit pool, and roughly one event's caption ingest fills a day. This mirrors the email
  sender decision: a self-hoster brings their own Google Cloud project and their own quota, while
  organizations onboarded onto a shared instance compete for one. If shared hosting becomes real,
  either the audit gets done or video work needs per-organization pacing.
- **Caption upload fidelity.** Round-tripping captions through the host can lose formatting or
  timing precision. Verify with one real video early, in M1.5, not at the end.
- **Deployment blocking M1.4.** Magic links need real email. If the deployment decision slips,
  M1.5 can be developed against a locally-created session, but M1.4 cannot be called done.
- **Write access to the videography team's uploads.** The larger risk than anything above. They
  upload to YouTube themselves, and M1.5, M1.7, and M1.3a all write back to those videos. If the
  credentials we hold cannot edit them, matching and speaker review still work but nothing can be
  published from here. Confirm before M1.2.
- **Videos arriving late or partially.** Processing is underway rather than finished, so the playlist
  will grow over days. Sync must be safe to re-run and must never delete on absence, which the
  upsert-only rule already covers, but it also means "every talk matched" is not a completion signal
  until the team says uploading is done.
