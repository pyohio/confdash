"""Program sync.

Pulls talks and speakers from whatever provider the event binds to `talk_source` and upserts them
locally. The adapter is resolved by capability, so this module never learns which provider it is
talking to.

Two rules, both load-bearing:

- **Idempotent upsert keyed on `(event, external_id)`.** Re-running changes nothing.
- **Never delete on absence.** A provider returning a short list, because of a filter change, a
  partial outage, or a credential scoped to fewer events, must not wipe local rows and the review
  state hanging off them. Absent records are counted and reported, not removed.
"""

from dataclasses import dataclass, field

import structlog
from django.db import transaction
from django.utils import timezone

from events.models import Event
from integrations.models import SyncRun
from integrations.providers.base import Capability, SpeakerRecord, TalkRecord
from integrations.resolver import resolve_adapter
from program.models import Speaker, Talk, TalkSpeaker

logger = structlog.get_logger(__name__)

# Fields overwritten from the provider on every sync. Anything not listed is either local state or
# an identifier, and must survive a re-sync untouched.
SPEAKER_SYNCED_FIELDS = ("name", "email", "biography", "avatar_url")
TALK_SYNCED_FIELDS = (
    "title",
    "abstract",
    "description",
    "duration_minutes",
    "session_type",
    "state",
    "do_not_record",
)


@dataclass
class SyncResult:
    speakers_created: int = 0
    speakers_updated: int = 0
    talks_created: int = 0
    talks_updated: int = 0
    links_created: int = 0
    links_removed: int = 0
    # Local rows the provider did not mention. Reported, never deleted.
    speakers_absent: int = 0
    talks_absent: int = 0
    absent_talk_ids: list[str] = field(default_factory=list)

    def as_counts(self) -> dict[str, int]:
        """Flat counts for `SyncRun.counts`, which is a plain JSON dict."""
        return {
            "speakers_created": self.speakers_created,
            "speakers_updated": self.speakers_updated,
            "talks_created": self.talks_created,
            "talks_updated": self.talks_updated,
            "links_created": self.links_created,
            "links_removed": self.links_removed,
            "speakers_absent": self.speakers_absent,
            "talks_absent": self.talks_absent,
        }


def sync_program(event: Event) -> SyncResult:
    """Sync talks and speakers for one event, recording a `SyncRun` either way.

    Raises whatever the adapter raises after marking the run failed, so a caller sees real errors
    rather than a silent no-op.
    """
    run = SyncRun.objects.create(
        event=event,
        capability=Capability.TALK_SOURCE,
        provider="",
        started_at=timezone.now(),
    )
    log = logger.bind(event_slug=event.slug, organization_slug=event.organization.slug)

    try:
        adapter = resolve_adapter(event, Capability.TALK_SOURCE)
        run.provider = adapter.provider
        run.save(update_fields=["provider", "updated_at"])

        speaker_records = adapter.fetch_speakers()
        talk_records = adapter.fetch_talks()

        with transaction.atomic():
            result = _apply(event, speaker_records, talk_records)
    except Exception as exc:
        run.finished_at = timezone.now()
        run.succeeded = False
        run.error = f"{type(exc).__name__}: {exc}"
        run.save(update_fields=["finished_at", "succeeded", "error", "updated_at"])
        log.error("program.sync_failed", error=str(exc))
        raise

    run.finished_at = timezone.now()
    run.succeeded = True
    run.counts = result.as_counts()
    run.save(update_fields=["finished_at", "succeeded", "counts", "updated_at"])

    log.info("program.synced", provider=adapter.provider, **result.as_counts())
    if result.absent_talk_ids:
        # Worth its own line: this is the case where a provider stopped reporting talks we hold.
        log.warning("program.talks_absent_from_provider", external_ids=result.absent_talk_ids)

    return result


def _apply(
    event: Event,
    speaker_records: list[SpeakerRecord],
    talk_records: list[TalkRecord],
) -> SyncResult:
    result = SyncResult()

    speakers_by_external_id = _upsert_speakers(event, speaker_records, result)
    _upsert_talks(event, talk_records, speakers_by_external_id, result)
    _count_absent(event, speaker_records, talk_records, result)

    return result


def _upsert_speakers(
    event: Event,
    records: list[SpeakerRecord],
    result: SyncResult,
) -> dict[str, Speaker]:
    existing = {s.external_id: s for s in Speaker.objects.filter(event=event)}
    by_external_id: dict[str, Speaker] = {}

    for record in records:
        speaker = existing.get(record.external_id)
        if speaker is None:
            speaker = Speaker.objects.create(
                event=event,
                external_id=record.external_id,
                **{name: getattr(record, name) for name in SPEAKER_SYNCED_FIELDS},
            )
            result.speakers_created += 1
        else:
            changed = _assign_changed(speaker, record, SPEAKER_SYNCED_FIELDS)
            if changed:
                speaker.save(update_fields=[*changed, "updated_at"])
                result.speakers_updated += 1
        by_external_id[record.external_id] = speaker

    return by_external_id


def _upsert_talks(
    event: Event,
    records: list[TalkRecord],
    speakers_by_external_id: dict[str, Speaker],
    result: SyncResult,
) -> None:
    existing = {t.external_id: t for t in Talk.objects.filter(event=event)}

    for record in records:
        talk = existing.get(record.external_id)
        if talk is None:
            talk = Talk.objects.create(
                event=event,
                external_id=record.external_id,
                **{name: getattr(record, name) for name in TALK_SYNCED_FIELDS},
            )
            result.talks_created += 1
        else:
            changed = _assign_changed(talk, record, TALK_SYNCED_FIELDS)
            if changed:
                talk.save(update_fields=[*changed, "updated_at"])
                result.talks_updated += 1

        _sync_links(talk, record, speakers_by_external_id, event, result)


def _sync_links(
    talk: Talk,
    record: TalkRecord,
    speakers_by_external_id: dict[str, Speaker],
    event: Event,
    result: SyncResult,
) -> None:
    """Reconcile a talk's speaker links to exactly what the provider reports.

    Links are the one thing that *is* removed on absence, unlike rows. A speaker dropping off a talk
    is a real editorial change with no local state attached to the link, and leaving a stale link
    would email the wrong person a review invitation.
    """
    wanted: set[str] = set()
    for external_id in record.speaker_external_ids:
        speaker = speakers_by_external_id.get(external_id)
        if speaker is None:
            # The provider referenced a speaker it did not include in the speaker list. Fall back to
            # a local lookup rather than dropping the link silently.
            speaker = Speaker.objects.filter(event=event, external_id=external_id).first()
            if speaker is None:
                logger.warning(
                    "program.link_speaker_missing",
                    event_slug=event.slug,
                    talk_external_id=record.external_id,
                    speaker_external_id=external_id,
                )
                continue
            speakers_by_external_id[external_id] = speaker
        wanted.add(str(speaker.pk))

    current = {str(pk) for pk in talk.talk_speakers.values_list("speaker_id", flat=True)}

    for speaker_pk in wanted - current:
        TalkSpeaker.objects.create(talk=talk, speaker_id=speaker_pk)
        result.links_created += 1

    stale = current - wanted
    if stale:
        removed, _ = talk.talk_speakers.filter(speaker_id__in=stale).delete()
        result.links_removed += removed


def _count_absent(
    event: Event,
    speaker_records: list[SpeakerRecord],
    talk_records: list[TalkRecord],
    result: SyncResult,
) -> None:
    """Report local rows the provider did not mention, without touching them."""
    seen_speakers = {r.external_id for r in speaker_records}
    seen_talks = {r.external_id for r in talk_records}

    absent_talks = list(
        Talk.objects.filter(event=event).exclude(external_id__in=seen_talks).values_list("external_id", flat=True)
    )
    result.talks_absent = len(absent_talks)
    result.absent_talk_ids = sorted(absent_talks)

    result.speakers_absent = Speaker.objects.filter(event=event).exclude(external_id__in=seen_speakers).count()


def _assign_changed(instance, record, field_names: tuple[str, ...]) -> list[str]:
    """Copy record fields onto the instance, returning the names that actually changed."""
    changed = []
    for name in field_names:
        new = getattr(record, name)
        if getattr(instance, name) != new:
            setattr(instance, name, new)
            changed.append(name)
    return changed
