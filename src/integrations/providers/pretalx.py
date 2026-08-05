"""Pretalx as a `talk_source`.

Hosted pretalx.com, versioned API. Ported from the `pyohio-cli` importer, which is the reference
for endpoint shapes and pagination.

One confirmed-submissions call with `expand=speakers,submission_type` returns talks and their
speakers together, so a full program sync is a single paginated fetch.

Adapters never touch the ORM: this returns the provider-neutral records from `base` and the sync
service owns all persistence.
"""

from typing import Any

import httpx

from integrations.providers.base import (
    BaseAdapter,
    Capability,
    ConfigKey,
    SpeakerRecord,
    TalkRecord,
)
from integrations.registry import register

# A Pretalx submission carries far more than a talk: reviewer scores, review comments, organizer
# notes, custom-question answers, and `invitation_token`, a credential that can claim the submission.
# Mapping onto `TalkRecord` and `SpeakerRecord` discards all of it, which is why those records have no
# catch-all payload field. See the note in `base`.


def _localized(value: Any) -> str:
    """Flatten a Pretalx localized field.

    Some fields come back as a plain string and others as `{"en": "..."}` depending on the endpoint
    and the event's locale settings, so callers cannot assume either.
    """
    if isinstance(value, dict):
        return value.get("en") or next((v for v in value.values() if v), "")
    return value or ""


@register
class PretalxTalkSource(BaseAdapter):
    capability = Capability.TALK_SOURCE
    provider = "pretalx"

    connection_config_keys = (
        ConfigKey(
            name="base_url",
            required=False,
            help_text="Override for self-hosted Pretalx. Defaults to https://pretalx.com.",
        ),
    )
    credential_keys = (
        ConfigKey(
            name="api_token",
            required=True,
            secret=True,
            help_text="Pretalx API token. Sent as `Authorization: Token <token>`.",
        ),
    )
    event_config_keys = (
        ConfigKey(
            name="event_id",
            required=True,
            help_text="Pretalx event slug, e.g. 'pyohio-2026'.",
        ),
    )

    DEFAULT_BASE_URL = "https://pretalx.com"
    TIMEOUT = 30.0

    @property
    def base_url(self) -> str:
        root = (self.config_value("base_url") or self.DEFAULT_BASE_URL).rstrip("/")
        return f"{root}/api/events/{self.config_value('event_id')}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.credential('api_token')}",
            # The API is versioned and unpinned requests can change shape under us.
            "Pretalx-Version": "v1",
        }

    def _get_all_pages(self, path: str) -> list[dict[str, Any]]:
        """Follow Pretalx's `next` cursor, accumulating `results`."""
        url: str | None = f"{self.base_url}{path}"
        results: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.TIMEOUT, headers=self.headers) as client:
            while url:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                results.extend(payload["results"])
                url = payload.get("next")
        return results

    def check(self) -> None:
        """Confirm the token works and the event slug resolves.

        Hits the event detail endpoint rather than listing submissions, so verifying a connection
        stays cheap and does not depend on the CFP having any confirmed content yet.
        """
        with httpx.Client(timeout=self.TIMEOUT, headers=self.headers) as client:
            client.get(self.base_url + "/").raise_for_status()

    def _confirmed_submissions(self) -> list[dict[str, Any]]:
        return self._get_all_pages("/submissions/?state=confirmed&expand=speakers,submission_type")

    def fetch_talks(self) -> list[TalkRecord]:
        return [self._to_talk(submission) for submission in self._confirmed_submissions()]

    def fetch_speakers(self) -> list[SpeakerRecord]:
        """Speakers on confirmed submissions, deduplicated.

        Taken from the expanded submissions rather than `/speakers/`, which also returns speakers
        whose talks were rejected or withdrawn. Those people have no business in a video-review
        database.
        """
        seen: dict[str, SpeakerRecord] = {}
        for submission in self._confirmed_submissions():
            for speaker in submission.get("speakers") or []:
                record = self._to_speaker(speaker)
                seen.setdefault(record.external_id, record)
        return list(seen.values())

    def _to_talk(self, submission: dict[str, Any]) -> TalkRecord:
        submission_type = submission.get("submission_type") or {}
        return TalkRecord(
            external_id=submission["code"],
            title=_localized(submission.get("title")),
            # Pretalx exposes `description` here and no `abstract`, so the abstract stays empty
            # rather than being faked from the description.
            abstract=_localized(submission.get("abstract")),
            description=_localized(submission.get("description")),
            duration_minutes=submission.get("duration"),
            session_type=_localized(submission_type.get("name")),
            state=submission.get("state") or "",
            speaker_external_ids=[s["code"] for s in submission.get("speakers") or []],
            do_not_record=bool(submission.get("do_not_record")),
        )

    def _to_speaker(self, speaker: dict[str, Any]) -> SpeakerRecord:
        return SpeakerRecord(
            external_id=speaker["code"],
            name=speaker.get("name") or "",
            email=speaker.get("email") or "",
            biography=_localized(speaker.get("biography")),
            avatar_url=speaker.get("avatar_url") or "",
        )
