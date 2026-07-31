"""Capability protocols and the provider-neutral records adapters return.

The rule that makes the abstraction real: nothing outside `integrations/providers/` may import a
provider module or branch on a provider name. Application code asks for a capability and gets
whatever adapter the event is bound to.

Adapters are thin. They call a remote API over httpx and return these dataclasses. They never
touch the ORM: sync services own all persistence. So a provider bug cannot corrupt local state,
and an adapter is testable with recorded fixtures and no database.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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
# Every record carries `raw`, the provider payload it came from. That lands in the model's `raw`
# JSONField, which makes a sync bug diagnosable after the fact and lets a later field addition
# backfill without re-fetching from the provider.


@dataclass(kw_only=True)
class SpeakerRecord:
    external_id: str
    name: str
    email: str = ""
    biography: str = ""
    avatar_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


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
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class VideoRecord:
    external_id: str
    title: str
    description: str = ""
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    duration_seconds: int | None = None
    published_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class CaptionRecord:
    language: str
    content: str
    external_id: str = ""
    is_draft: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


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
    """Video listing, captions, and privacy control. YouTube today."""

    def list_videos(self) -> list[VideoRecord]: ...

    def fetch_captions(self, external_id: str) -> list[CaptionRecord]: ...

    def upload_captions(self, external_id: str, track: CaptionRecord) -> str:
        """Upload or replace a caption track. Returns the provider's track id."""
        ...

    def set_privacy(self, external_id: str, status: PrivacyStatus) -> None: ...


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
