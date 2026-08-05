"""Video-host writes: what to enqueue, and what executing it means.

Two halves, and the split is the point.

The request functions are called from views and services. They record intent and nothing else: no
provider call, no local state change. Call them inside the transaction that makes the local change they
follow from.

The handlers run at drain time. Each one re-checks that the write is still allowed, performs it, and
moves `Video` to whatever the provider confirmed. `publication_state` and `privacy_status` are only ever
written here, which is how "local state records confirmed provider state, never intent" stays true
rather than being a rule everyone has to remember.

Guards are deliberately at execution, not enqueue. A talk can be marked do-not-record and an approval
can be withdrawn between the two, and a queue trusting its own stale snapshot would publish something
it should not.
"""

import hashlib

import structlog

from integrations.models import ProviderWrite
from integrations.outbox import WriteOutcome, enqueue, handles
from integrations.providers.base import Capability, CaptionRecord, PrivacyStatus, WriteRejected
from videos.models import Video

logger = structlog.get_logger(__name__)

# Privacy values that need no permission to set. Making something less visible is a retraction, and a
# retraction must never be blocked by the same guard that gates publication.
_RETRACTIONS = frozenset({PrivacyStatus.PRIVATE, PrivacyStatus.UNLISTED})


def content_hash(text: str) -> str:
    """Identity of a caption body, for telling a real edit from a re-save.

    Not a security boundary, so the choice of digest is about stability rather than strength.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Requesting -------------------------------------------------------------


def request_privacy(video: Video, status: PrivacyStatus | str, *, user=None, not_before=None) -> ProviderWrite:
    """Ask for a video's visibility to change.

    Note what this does *not* do: it does not set `publication_state`. Publication becomes true when
    YouTube says the video is public, not when someone clicks a button.
    """
    status = PrivacyStatus(status)
    return enqueue(
        event=video.event,
        capability=Capability.VIDEO_HOST,
        operation=ProviderWrite.Operation.SET_PRIVACY,
        target_external_id=video.external_id,
        desired={"privacy_status": str(status)},
        requested_by=user,
        not_before=not_before,
    )


def request_publication(video: Video, *, user=None, not_before=None) -> ProviderWrite:
    """Queue a video for release.

    Eligibility is checked here as a courtesy to the caller, so a button that cannot work says so
    immediately, and again when the write executes, which is the check that actually protects anything.
    """
    if not video.may_be_published:
        raise ValueError("This video is not eligible for publication.")
    return request_privacy(video, PrivacyStatus.PUBLIC, user=user, not_before=not_before)


def request_caption_upload(video: Video, *, language: str, content: str, user=None) -> ProviderWrite:
    """Push a corrected caption track.

    The content rides in `desired` rather than being fetched at drain time, so what gets uploaded is
    exactly what the reviewer approved, even if the source has changed since.
    """
    if not content.strip():
        raise ValueError("Refusing to upload an empty caption track.")

    return enqueue(
        event=video.event,
        capability=Capability.VIDEO_HOST,
        operation=ProviderWrite.Operation.UPLOAD_CAPTIONS,
        target_external_id=video.external_id,
        desired={"language": language, "content": content, "content_hash": content_hash(content)},
        requested_by=user,
    )


# --- Executing --------------------------------------------------------------


def _video_for(write: ProviderWrite) -> Video:
    """The video a write targets, or a permanent rejection if it is gone.

    A missing row means the video left the playlist between enqueue and drain. There is nothing to
    retry, and guessing at intent for a video we no longer track is exactly the divergence this queue
    exists to prevent.
    """
    video = (
        Video.objects.select_related("talk", "event")
        .filter(event_id=write.event_id, external_id=write.target_external_id)
        .first()
    )
    if video is None:
        raise WriteRejected(f"No local video for {write.target_external_id}; it may have left the playlist.")
    return video


@handles(Capability.VIDEO_HOST, ProviderWrite.Operation.SET_PRIVACY)
def set_privacy(write: ProviderWrite, adapter) -> WriteOutcome:
    """Change visibility, then confirm by reading the video back.

    Confirmed by re-reading because `videos.list` is a single unit and publication is the write whose
    divergence matters most: believing a video is public when it is unlisted means a speaker is told
    their talk is out when nobody can find it. Cheap insurance.
    """
    video = _video_for(write)
    desired = PrivacyStatus(write.desired["privacy_status"])

    if desired not in _RETRACTIONS and not video.may_be_published:
        raise WriteRejected(
            f"{video} is no longer eligible for publication "
            f"(review_state={video.review_state}, do_not_record={video.talk.do_not_record if video.talk else False})."
        )

    adapter.set_privacy(video.external_id, desired)

    observed = adapter.fetch_video(video.external_id)
    if observed is None:
        raise WriteRejected(f"{video.external_id} is not readable after the write; cannot confirm.")
    if PrivacyStatus(observed.privacy_status) != desired:
        # Retryable, not permanent: the provider accepted the call and has not applied it yet, which
        # eventual consistency makes a normal outcome rather than a failure.
        raise RuntimeError(f"Provider still reports {observed.privacy_status}, expected {desired}.")

    video.privacy_status = str(desired)
    video.publication_state = (
        Video.PublicationState.PUBLISHED if desired == PrivacyStatus.PUBLIC else Video.PublicationState.UNPUBLISHED
    )
    video.save(update_fields=["privacy_status", "publication_state", "updated_at"])

    logger.info(
        "video.privacy_confirmed",
        event_slug=video.event.slug,
        video_external_id=video.external_id,
        privacy_status=str(desired),
    )
    return WriteOutcome(result={"privacy_status": str(desired), "confirmed_by": "read_back"})


@handles(Capability.VIDEO_HOST, ProviderWrite.Operation.UPLOAD_CAPTIONS)
def upload_captions(write: ProviderWrite, adapter) -> WriteOutcome:
    """Replace a caption track, trusting the returned track id.

    Not confirmed by reading back, unlike privacy: verifying means `captions.list` plus
    `captions.download` at 250 units, more than the write itself cost. The returned id plus the stored
    content hash answers the only question anyone asks later, which is whether this exact text was
    pushed.
    """
    video = _video_for(write)

    if video.talk is not None and video.talk.do_not_record:
        raise WriteRejected(f"{video.talk} is marked do-not-record; not writing captions to its video.")

    track = CaptionRecord(language=write.desired["language"], content=write.desired["content"])
    track_id = adapter.upload_captions(video.external_id, track)

    logger.info(
        "video.captions_uploaded",
        event_slug=video.event.slug,
        video_external_id=video.external_id,
        language=track.language,
    )
    return WriteOutcome(
        result={
            "track_id": track_id,
            "language": track.language,
            "content_hash": write.desired.get("content_hash", ""),
        }
    )
