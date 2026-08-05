"""Fake adapters for tests.

Sync services and the resolver are tested against these, so no test needs HTTP or recorded
fixtures to exercise persistence, validation, and error paths. Real adapters get tested
separately against recorded provider responses.

These are registered on demand by the `fake_providers` fixture rather than at import time, so
they never leak into the registry a running application sees.
"""

from dataclasses import replace

from integrations.providers.base import (
    BaseAdapter,
    Capability,
    CaptionRecord,
    ConfigKey,
    PrivacyStatus,
    SpeakerRecord,
    TalkRecord,
    VideoRecord,
)


class FakeTalkSource(BaseAdapter):
    """Talk source that returns whatever is put on the class."""

    capability = Capability.TALK_SOURCE
    provider = "fake"

    connection_config_keys = (ConfigKey(name="api_base_url", required=False),)
    credential_keys = (ConfigKey(name="api_token", required=True, secret=True),)
    event_config_keys = (ConfigKey(name="event_id", required=True),)

    speakers: list[SpeakerRecord] = []
    talks: list[TalkRecord] = []
    check_error: Exception | None = None

    def check(self) -> None:
        if self.check_error:
            raise self.check_error

    def fetch_speakers(self) -> list[SpeakerRecord]:
        return list(self.speakers)

    def fetch_talks(self) -> list[TalkRecord]:
        return list(self.talks)


class FakeVideoHost(BaseAdapter):
    """Video host that records the mutations asked of it, so tests can assert on them."""

    capability = Capability.VIDEO_HOST
    provider = "fake"

    credential_keys = (ConfigKey(name="refresh_token", required=True, secret=True),)
    event_config_keys = (ConfigKey(name="playlist_id", required=True),)

    videos: list[VideoRecord] = []
    captions: dict[str, list[CaptionRecord]] = {}

    # Failures a test can ask for, since the outbox's whole job is handling them.
    set_privacy_error: Exception | None = None
    upload_captions_error: Exception | None = None
    # When true, `set_privacy` records the call but does not change what `fetch_video` reports, standing
    # in for a provider that accepted a write and has not applied it.
    ignore_privacy_writes: bool = False

    # Recorded on the class, not the instance, because the outbox resolves its own adapter: a test has
    # no reference to the object that performed the write, only to what the write did.
    uploaded_captions: list[tuple[str, CaptionRecord]] = []
    privacy_changes: list[tuple[str, PrivacyStatus]] = []

    def check(self) -> None:
        return None

    def list_videos(self) -> list[VideoRecord]:
        return list(self.videos)

    def fetch_video(self, external_id: str) -> VideoRecord | None:
        for video in self.videos:
            if video.external_id == external_id:
                return video
        return None

    def fetch_captions(self, external_id: str) -> list[CaptionRecord]:
        return list(self.captions.get(external_id, []))

    def upload_captions(self, external_id: str, track: CaptionRecord) -> str:
        if self.upload_captions_error:
            raise self.upload_captions_error
        self.uploaded_captions.append((external_id, track))
        return f"fake-caption-{external_id}-{track.language}"

    def set_privacy(self, external_id: str, status: PrivacyStatus) -> None:
        if self.set_privacy_error:
            raise self.set_privacy_error
        self.privacy_changes.append((external_id, status))
        if self.ignore_privacy_writes:
            return
        # Apply it, so a handler that confirms by reading back sees what it asked for.
        for index, video in enumerate(self.videos):
            if video.external_id == external_id:
                self.videos[index] = replace(video, privacy_status=status)
                return

    @staticmethod
    def parse_external_id(value: str) -> str:
        """Minimal stand-in for a real provider's URL handling.

        Enough to prove that callers delegate here rather than parsing references themselves.
        """
        value = value.strip()
        if not value:
            raise ValueError("Enter a video id or URL.")
        if "v=" in value:
            return value.split("v=", 1)[1].split("&", 1)[0]
        return value.rstrip("/").rsplit("/", 1)[-1]


class IncompleteTalkSource:
    """Missing `fetch_talks`, to prove the registry rejects a half-built adapter."""

    capability = Capability.TALK_SOURCE
    provider = "incomplete"

    connection_config_keys = ()
    credential_keys = ()
    event_config_keys = ()

    def check(self) -> None:
        return None

    def fetch_speakers(self) -> list[SpeakerRecord]:
        return []
