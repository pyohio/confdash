"""Video services.

Matching is here rather than in the admin so the eventual HTMX confirm queue and the admin share one
implementation, and so the audit stamps cannot be forgotten by one caller.
"""

import structlog
from django.utils import timezone

from integrations.providers.base import Capability
from integrations.resolver import IntegrationNotConfigured, get_binding
from videos.models import Video

logger = structlog.get_logger(__name__)


def normalize_reference(event, value: str) -> str:
    """Turn whatever an organizer pasted into the provider's video id.

    Delegates to the event's `video_host`, because recognizing a watch URL is provider knowledge and
    nothing outside `integrations/providers/` should know what one looks like.

    With no video host configured the value is accepted as a bare id, so manual linking works before
    any OAuth setup, which is the whole point of the manual path. But a URL is *rejected* in that case
    rather than stored: `external_id` is unique per event, so storing a watch URL guarantees a
    duplicate row the moment a real sync reports the same video by its actual id.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("Enter a video id or URL.")

    parse = _host_parser(event)
    if parse is not None:
        return parse(value)

    if _looks_like_a_url(value):
        raise ValueError(
            "This event has no video host configured, so a URL cannot be interpreted. "
            "Paste the bare video id instead, or configure the video host first."
        )
    return value


def _host_parser(event):
    """The event's video-host reference parser, or None if there is nothing to ask."""
    try:
        binding = get_binding(event, Capability.VIDEO_HOST)
    except IntegrationNotConfigured:
        return None

    try:
        adapter_class = binding.connection.adapter_class
    except Exception:
        # An unregistered provider is reported against the connection itself; do not block linking.
        return None

    return getattr(adapter_class, "parse_external_id", None)


def _looks_like_a_url(value: str) -> bool:
    """Deliberately crude, and deliberately not provider-specific.

    This does not try to understand any provider's URLs, only to notice that it has been handed
    something no provider was available to interpret.
    """
    return "://" in value or "/" in value or "?" in value or any(c.isspace() for c in value)


def link_video_to_talk(video: Video, talk, *, user=None) -> Video:
    """Match a video to a talk, stamping who did it and when.

    Raises `ValueError` on a cross-event link rather than relying on the caller to check, since this is
    the one operation where getting tenancy wrong attaches one event's video to another's talk.
    """
    if talk.event_id != video.event_id:
        raise ValueError("Talk belongs to a different event than this video.")

    video.talk = talk
    video.standalone = False
    video.matched_by = user
    video.matched_at = timezone.now()
    video.save(update_fields=["talk", "standalone", "matched_by", "matched_at", "updated_at"])

    logger.info(
        "video.matched",
        event_slug=video.event.slug,
        video_external_id=video.external_id,
        talk_external_id=talk.external_id,
    )
    return video


def mark_standalone(video: Video, *, user=None) -> Video:
    """Record that a video deliberately has no talk.

    A settled outcome, not a failure: a playlist contains welcomes, closing remarks, and hallway clips
    alongside the talks. Without this the confirm queue would re-present them forever.
    """
    video.talk = None
    video.standalone = True
    video.matched_by = user
    video.matched_at = timezone.now()
    video.save(update_fields=["talk", "standalone", "matched_by", "matched_at", "updated_at"])

    logger.info("video.marked_standalone", event_slug=video.event.slug, video_external_id=video.external_id)
    return video


def unmatch(video: Video) -> Video:
    """Undo a match, returning the video to the queue.

    Clears the audit stamps too: leaving them would claim someone decided the current state, and the
    current state is now "undecided".
    """
    video.talk = None
    video.standalone = False
    video.matched_by = None
    video.matched_at = None
    video.save(update_fields=["talk", "standalone", "matched_by", "matched_at", "updated_at"])

    logger.info("video.unmatched", event_slug=video.event.slug, video_external_id=video.external_id)
    return video
