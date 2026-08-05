"""Capability protocols and the provider-neutral records adapters return.

The rule that makes the abstraction real: nothing outside `integrations/providers/` may import a
provider module or branch on a provider name. Application code asks for a capability and gets
whatever adapter the event is bound to.

Adapters are thin. They call a remote API over httpx and return these dataclasses. They never
touch the ORM: sync services own all persistence. So a provider bug cannot corrupt local state,
and an adapter is testable with recorded fixtures and no database.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """Base for the failures adapters are expected to raise, as opposed to bugs."""


class QuotaExceeded(ProviderError):
    """The provider refused the call because the caller is out of allowance.

    Carries `retry_after` because the right wait is provider knowledge, not queue knowledge: YouTube's
    allowance returns at midnight Pacific whatever the backoff curve says, and a generic exponential
    retry burns units rediscovering the wall. The adapter that knows the reset schedule sets it; the
    outbox defers until then rather than guessing.
    """

    def __init__(self, message: str = "", *, retry_after: datetime | None = None):
        super().__init__(message or "Provider quota exceeded.")
        self.retry_after = retry_after


class WriteRejected(ProviderError):
    """The provider refused the write itself, and retrying it unchanged will not help.

    Distinct from a transient failure: a malformed caption track or a video the credentials cannot
    reach is a permanent outcome, and a queue that retries it wastes allowance and hides the problem.
    """


class Capability(StrEnum):
    """The external services the app depends on, named by what they do, not who provides them.

    A plain StrEnum rather than `models.TextChoices` so this module stays free of Django imports
    and adapters remain testable without a configured settings module. Django's `choices=` cannot
    take an enum class directly, so model fields use `Capability.choices()`.
    """

    TALK_SOURCE = "talk_source"
    TICKETING = "ticketing"
    VIDEO_HOST = "video_host"

    @property
    def label(self) -> str:
        return _CAPABILITY_LABELS[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(member.value, member.label) for member in cls]


_CAPABILITY_LABELS = {
    Capability.TALK_SOURCE: "Talk source",
    Capability.TICKETING: "Ticketing",
    Capability.VIDEO_HOST: "Video host",
}


class PrivacyStatus(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


@dataclass(frozen=True, kw_only=True)
class ConfigKey:
    """One config or credential key an adapter needs, for validation before first sync."""

    name: str
    required: bool = True
    secret: bool = False
    help_text: str = ""


# --- Records ----------------------------------------------------------------
#
# These are the whole contract between a provider and the rest of the app: an adapter maps the
# provider's response onto exactly these fields and discards the rest. Nothing carries a snapshot of
# the original payload.
#
# That is a deliberate reversal of the original design, which stored the provider payload in a `raw`
# JSONField for diagnosability and for backfilling a later field addition without re-fetching.
# Neither justified the cost. A real Pretalx submission ships reviewer scores, organizer notes,
# custom-question answers, and an `invitation_token` that can claim the submission. Sync is
# idempotent, so a re-run backfills a newly mapped field anyway, and `SyncRun` plus logging cover
# diagnosis.
#
# This is not a decision that CFP review data may never be stored. A CFP surface for the program
# committee is a real future feature that wants exactly that data. It gets its own models, gated
# behind `Scope.PROGRAM`, rather than riding along on `Talk` and `Speaker`, which speakers reach to
# review their own videos. The sensitivity boundary is a model boundary, enforced by scope, not a
# field filter on a shared row.
#
# Consequence, and the point: anything the app needs must be an explicit field here. Adding one is a
# deliberate act, and a provider adding a field of its own changes nothing until someone maps it.


@dataclass(kw_only=True)
class SpeakerRecord:
    external_id: str
    name: str
    email: str = ""
    biography: str = ""
    avatar_url: str = ""


@dataclass(kw_only=True)
class TalkRecord:
    external_id: str
    title: str
    abstract: str = ""
    description: str = ""
    duration_minutes: int | None = None
    session_type: str = ""
    state: str = ""
    scheduled_start: str | None = None
    scheduled_end: str | None = None
    speaker_external_ids: list[str] = field(default_factory=list)
    # Speaker or organizer opt-out of recording. A publication guard: a talk marked this way must
    # never be released, whatever the review state says.
    do_not_record: bool = False


@dataclass(kw_only=True)
class VideoRecord:
    external_id: str
    title: str
    description: str = ""
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    duration_seconds: int | None = None
    published_at: str | None = None


@dataclass(kw_only=True)
class CaptionRecord:
    language: str
    content: str
    external_id: str = ""
    is_draft: bool = False


# --- Capability protocols ---------------------------------------------------


@runtime_checkable
class ProviderAdapter(Protocol):
    """Shared surface every adapter declares, used by the registry and by binding validation."""

    capability: Capability
    provider: str

    # Keys the adapter needs, split by where they live and whether they are secret.
    connection_config_keys: tuple[ConfigKey, ...]
    credential_keys: tuple[ConfigKey, ...]
    event_config_keys: tuple[ConfigKey, ...]

    def check(self) -> None:
        """Verify the credentials work. Raises on failure.

        Backs the admin's "verify connection" action, so an organizer finds out that a token is
        wrong at setup time rather than at first sync.
        """
        ...


@runtime_checkable
class TalkSource(ProviderAdapter, Protocol):
    """Talks, speakers, and schedule. Pretalx today; Sessionize or PaperCall plausibly later."""

    def fetch_speakers(self) -> list[SpeakerRecord]: ...

    def fetch_talks(self) -> list[TalkRecord]: ...


@runtime_checkable
class Ticketing(ProviderAdapter, Protocol):
    """Registrations and attendee data. Tito today; Eventbrite plausibly later. M2 scope."""

    def fetch_registrations(self) -> list[Any]: ...


@runtime_checkable
class VideoHost(ProviderAdapter, Protocol):
    """Video listing, captions, and privacy control. YouTube today.

    No upload method, deliberately. Recordings are uploaded by whoever produces them, outside this
    app; a video host is something this app reads from and annotates, never publishes to.
    """

    def list_videos(self) -> list[VideoRecord]: ...

    def fetch_video(self, external_id: str) -> VideoRecord | None:
        """One video by id, or None if the provider does not have it.

        Separate from `list_videos` because confirming a write should not cost a playlist scan. A
        privacy change is confirmed by reading the video back, and that read has to be cheap enough
        to do after every one.
        """
        ...

    def fetch_captions(self, external_id: str) -> list[CaptionRecord]: ...

    def upload_captions(self, external_id: str, track: CaptionRecord) -> str:
        """Upload or replace a caption track. Returns the provider's track id."""
        ...

    def set_privacy(self, external_id: str, status: PrivacyStatus) -> None: ...

    @staticmethod
    def parse_external_id(value: str) -> str:
        """Turn a pasted reference into this provider's video id.

        Organizers link videos by pasting whatever they have to hand, which is usually a watch URL
        rather than a bare id. Recognizing those forms is provider knowledge, so it lives here rather
        than in a form: nothing outside `providers/` should know what a YouTube URL looks like.

        A static method so manual linking works before any credentials are configured. Raises
        `ValueError` on something unrecognizable.
        """
        ...


CAPABILITY_PROTOCOLS: dict[Capability, type] = {
    Capability.TALK_SOURCE: TalkSource,
    Capability.TICKETING: Ticketing,
    Capability.VIDEO_HOST: VideoHost,
}


class BaseAdapter:
    """Optional convenience base: holds resolved config and credentials.

    Adapters may subclass this or just satisfy the protocol. Subclassing buys the constructor
    and the config accessors, nothing more.
    """

    capability: Capability
    provider: str

    connection_config_keys: tuple[ConfigKey, ...] = ()
    credential_keys: tuple[ConfigKey, ...] = ()
    event_config_keys: tuple[ConfigKey, ...] = ()

    def __init__(self, *, config: dict[str, Any], credentials: dict[str, Any]):
        self.config = config
        self.credentials = credentials

    def config_value(self, name: str, default: Any = None) -> Any:
        return self.config.get(name, default)

    def credential(self, name: str) -> Any:
        return self.credentials.get(name)

    def check(self) -> None:
        raise NotImplementedError
