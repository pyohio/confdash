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

- `YouTubeVideoHost` implementing `list_videos`, reading a playlist from the binding config.
- OAuth credentials seeded out of band into a `ProviderConnection` (see decisions: the
  self-service consent flow is deferred).
- `sync_videos` service: upsert `Video` rows, unmatched, with `privacy_status` as reported.
- `just manage sync_videos --event <slug>`.

Done when the playlist's videos are `Video` rows with `talk` still null.

### M1.3 Matching

The organizer step that turns unmatched videos into talk-linked ones. Videos carry placeholder
titles set at upload, so matching is title-based with human confirmation.

- Suggestion pass: normalized fuzzy match of video title against talk titles, producing ranked
  candidates with scores. Deterministic and testable, no ML.
- Organizer review screen: unmatched videos with suggested talks, confirm or override,
  server-rendered with HTMX. This is the first place the admin is the wrong tool — a
  side-by-side confirm queue is the whole job.
- Bulk-accept high-confidence matches, with an undo.

Done when every video for an event is either matched or explicitly marked unmatchable.

Open: the exact placeholder title convention PyOhio 2026 uploads will use. The matcher should
not assume a format it has not seen; confirm the convention before tuning the scorer.

### M1.4 Speaker authentication

Passwordless login, needed before anyone can be invited.

- Magic-link request and consume flow, tokens hashed at rest, single-use, expiring.
- `Speaker.user` resolution by email on first login.
- Authorization: a speaker may only see videos for talks they are a speaker on.
- Rate limiting on link requests, and no user-existence disclosure in the response.

Done when a speaker can click an emailed link and land on their own video, and cannot reach
anyone else's.

Blocked on: a deployment target with working outbound email and a real hostname.

### M1.5 Speaker review

The speaker-facing surface.

- Video player with the unlisted video embedded.
- Caption viewing, with a transcript view aligned to playback.
- Caption editing and re-upload, writing a new `CaptionTrack` row and pushing to the host.
- Report a problem: a comment, optionally timecoded.
- Approve, which sets `review_state` and stamps `approved_at` / `approved_by`.

Done when a speaker can view, correct captions, and approve, and the corrected captions are
live on the host.

Open: caption editor scope. A plain textarea over the raw SRT/VTT is hours of work and is
genuinely usable by a technical audience; a cue-by-cue editor with playback sync is a
significant build. Start with the former unless the audience argues otherwise.

### M1.6 Invitations

- Compose and send review invitations per speaker, per video.
- Templated email, org/event branding from settings.
- Organizer view of invitation status: sent, opened, approved, stale.
- Resend and bulk-send.

Done when an organizer can invite every matched talk's speakers in one action and see who has
not responded.

### M1.7 Publication

- Per-event release policy in `Event.settings`, resolving to org default:
  `immediate` (flip to public on approval) or `hold` (approve now, release together).
- Immediate: on approval, set privacy public via the adapter, update `publication_state`.
- Hold: a playlist-wide release action that publishes all approved videos, with a dry run.
- Guard: never publish a video that is not `approved`.
- Audit: who released what, when.

Done when both policies work end to end against YouTube and the guard is covered by tests.

## Sequencing

M1.1 and M1.2 are independent and can go in either order. M1.3 needs both. M1.4 gates M1.5 and
M1.6. M1.7 is last because it is the only irreversible step — a video made public cannot be
un-published without someone noticing.

## Risks

- **YouTube API quota.** The Data API has a daily quota; caption downloads and privacy updates
  are not free. Measure actual cost against a real playlist during M1.2 before assuming a
  full-event sync fits in a day's quota.
- **Caption upload fidelity.** Round-tripping captions through the host can lose formatting or
  timing precision. Verify with one real video early, in M1.5, not at the end.
- **Deployment blocking M1.4.** Magic links need real email. If the deployment decision slips,
  M1.5 can be developed against a locally-created session, but M1.4 cannot be called done.
- **Placeholder title convention.** M1.3's matcher quality depends entirely on it. Confirm
  before building the scorer.
