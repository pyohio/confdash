"""Pretalx adapter, against recorded response fixtures.

No live network: the fixtures were shaped from a real `pyohio-2026` response, with every value
replaced by a synthetic one. The sensitive keys are kept, populated with obvious `FIXTURE-` values,
precisely so the sanitization tests have something to catch.
"""

import json
from pathlib import Path

import httpx
import pytest

from integrations.providers.base import Capability
from integrations.providers.pretalx import PretalxTalkSource

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "pretalx"

REAL_HTTPX_CLIENT = httpx.Client

# Every field that must never leave the adapter, whatever Pretalx sends. `invitation_token` is a
# credential; the rest is confidential CFP review data or arbitrary custom-question answers. A CFP
# surface for the program committee will store these in its own organizer-only models.
FORBIDDEN_IN_RAW = (
    "invitation_token",
    "review_code",
    "reviews",
    "assigned_reviewers",
    "mean_score",
    "median_score",
    "internal_notes",
    "answers",
    "anonymised_data",
    "is_anonymised",
    "access_code",
    "invitations",
    "pending_state",
    "email",
)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def patched_httpx(monkeypatch):
    """Route the adapter's httpx.Client through a MockTransport serving queued fixtures."""

    def install(responses: list[dict]):
        remaining = list(responses)
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            assert request.headers["Authorization"] == "Token test-token"
            assert request.headers["Pretalx-Version"] == "v1"
            return httpx.Response(200, json=remaining.pop(0))

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            # Always the genuine client, captured at import: wrapping whatever `httpx.Client`
            # currently is would nest transports if a test installs twice.
            return REAL_HTTPX_CLIENT(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)
        return seen_urls

    return install


@pytest.fixture
def adapter():
    return PretalxTalkSource(
        config={"event_id": "pyohio-2026"},
        credentials={"api_token": "test-token"},
    )


class TestRegistration:
    def test_declares_the_talk_source_capability(self):
        assert PretalxTalkSource.capability == Capability.TALK_SOURCE
        assert PretalxTalkSource.provider == "pretalx"

    def test_is_registered_for_resolution(self):
        from integrations.registry import get_adapter_class

        assert get_adapter_class(Capability.TALK_SOURCE, "pretalx") is PretalxTalkSource


class TestUrlConstruction:
    def test_defaults_to_hosted_pretalx(self, adapter):
        assert adapter.base_url == "https://pretalx.com/api/events/pyohio-2026"

    def test_honors_a_self_hosted_base_url(self):
        adapter = PretalxTalkSource(
            config={"event_id": "conf-2026", "base_url": "https://cfp.example.org/"},
            credentials={"api_token": "t"},
        )
        assert adapter.base_url == "https://cfp.example.org/api/events/conf-2026"


class TestFetchTalks:
    def test_maps_the_fields_the_app_uses(self, adapter, patched_httpx):
        patched_httpx([load("submissions_confirmed.json")])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert set(talks) == {"SUB111", "SUB222", "SUB333"}
        talk = talks["SUB111"]
        assert talk.title == "Taming Async Python"
        assert talk.description == "A tour of asyncio, with the parts that bite."
        assert talk.duration_minutes == 30
        assert talk.session_type == "30 Minute Talk"
        assert talk.state == "confirmed"
        assert talk.speaker_external_ids == ["SPKAAA"]

    def test_abstract_is_empty_because_pretalx_does_not_send_one(self, adapter, patched_httpx):
        """Left empty rather than filled from the description, so nothing invents content."""
        patched_httpx([load("submissions_confirmed.json")])

        assert all(talk.abstract == "" for talk in adapter.fetch_talks())

    def test_keeps_every_speaker_on_a_multi_speaker_talk(self, adapter, patched_httpx):
        patched_httpx([load("submissions_confirmed.json")])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert talks["SUB222"].speaker_external_ids == ["SPKBBB", "SPKCCC"]

    def test_do_not_record_is_mapped(self, adapter, patched_httpx):
        """A publication guard, so it needs its own field rather than living in a payload blob."""
        patched_httpx([load("submissions_confirmed.json")])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert talks["SUB333"].do_not_record is True
        assert talks["SUB111"].do_not_record is False

    def test_follows_pagination(self, adapter, patched_httpx):
        seen = patched_httpx([load("submissions_page1.json"), load("submissions_page2.json")])

        talks = adapter.fetch_talks()

        assert [t.external_id for t in talks] == ["PAGE01", "PAGE02"]
        assert len(seen) == 2
        assert seen[1].endswith("page=2")


class TestFetchSpeakers:
    def test_returns_speakers_from_confirmed_submissions(self, adapter, patched_httpx):
        patched_httpx([load("submissions_confirmed.json")])

        speakers = {s.external_id: s for s in adapter.fetch_speakers()}

        assert set(speakers) == {"SPKAAA", "SPKBBB", "SPKCCC", "SPKDDD"}
        assert speakers["SPKAAA"].name == "Ada Fixture"
        assert speakers["SPKAAA"].email == "ada@example.org"
        assert speakers["SPKAAA"].biography == "Writes Python for a living."
        assert speakers["SPKAAA"].avatar_url == "https://example.com/avatars/ada.png"

    def test_deduplicates_speakers_appearing_on_several_talks(self, adapter, patched_httpx):
        payload = load("submissions_confirmed.json")
        # Same speaker on two submissions, which is what a repeat speaker looks like.
        payload["results"][1]["speakers"] = payload["results"][0]["speakers"]
        patched_httpx([payload])

        speakers = adapter.fetch_speakers()

        assert len(speakers) == len({s.external_id for s in speakers})


class TestOnlyMappedFieldsSurvive:
    """CFP review internals must not leave the adapter.

    The guarantee is structural rather than a filter: the records are dataclasses with a fixed field
    set, so there is nowhere for an unmapped field to go. These tests guard the structure itself,
    since the way this breaks is someone adding a catch-all payload field back.
    """

    def test_records_carry_no_catch_all_payload_field(self):
        from dataclasses import fields

        from integrations.providers.base import CaptionRecord, SpeakerRecord, TalkRecord, VideoRecord

        for record in (SpeakerRecord, TalkRecord, VideoRecord, CaptionRecord):
            names = {f.name for f in fields(record)}
            assert not names & {"raw", "payload", "data", "extra"}, f"{record.__name__} regained a payload field"

    def test_no_sensitive_field_name_appears_on_a_talk_record(self, adapter, patched_httpx):
        from dataclasses import asdict

        patched_httpx([load("submissions_confirmed.json")])

        for talk in adapter.fetch_talks():
            assert not set(asdict(talk)) & set(FORBIDDEN_IN_RAW)

    def test_no_sensitive_value_survives_into_any_record(self, adapter, patched_httpx):
        """The fixtures mark every sensitive value with `FIXTURE-`, so one string search covers it."""
        from dataclasses import asdict

        # Two queued responses: fetch_talks and fetch_speakers each make one request.
        patched_httpx([load("submissions_confirmed.json"), load("submissions_confirmed.json")])

        talks = adapter.fetch_talks()
        speakers = adapter.fetch_speakers()

        blob = json.dumps([asdict(r) for r in (*talks, *speakers)])
        assert "FIXTURE-" not in blob

    def test_a_field_pretalx_adds_later_is_ignored(self, adapter, patched_httpx):
        """A provider adding a field changes nothing until someone deliberately maps it."""
        from dataclasses import asdict

        payload = load("submissions_confirmed.json")
        payload["results"][0]["some_future_sensitive_field"] = "FIXTURE-should-not-be-persisted"
        patched_httpx([payload])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert "FIXTURE-" not in json.dumps(asdict(talks["SUB111"]))


class TestLocalizedFields:
    def test_flattens_a_localized_dict(self, adapter, patched_httpx):
        payload = load("submissions_confirmed.json")
        payload["results"][0]["title"] = {"en": "Localized Title"}
        payload["results"][0]["submission_type"]["name"] = {"en": "Long Talk"}
        patched_httpx([payload])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert talks["SUB111"].title == "Localized Title"
        assert talks["SUB111"].session_type == "Long Talk"

    def test_falls_back_to_any_locale_when_english_is_absent(self, adapter, patched_httpx):
        payload = load("submissions_confirmed.json")
        payload["results"][0]["title"] = {"de": "Deutscher Titel"}
        patched_httpx([payload])

        talks = {t.external_id: t for t in adapter.fetch_talks()}

        assert talks["SUB111"].title == "Deutscher Titel"


class TestCheck:
    def test_check_passes_on_a_reachable_event(self, adapter, patched_httpx):
        seen = patched_httpx([{"slug": "pyohio-2026"}])

        adapter.check()

        assert seen == ["https://pretalx.com/api/events/pyohio-2026/"]

    def test_check_raises_on_a_bad_token(self, adapter, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Invalid token."})

        real_client = httpx.Client
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda *a, **kw: real_client(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
        )

        with pytest.raises(httpx.HTTPStatusError):
            adapter.check()
