"""Program sync.

The two rules that carry the most weight get the most coverage: re-running changes nothing, and a
provider that stops reporting a talk never deletes it locally.
"""

import pytest

from integrations.models import SyncRun
from integrations.providers.base import Capability, SpeakerRecord, TalkRecord
from integrations.resolver import IntegrationNotConfigured
from program.models import Speaker, Talk, TalkSpeaker
from program.services import sync_program

pytestmark = pytest.mark.integration


def speaker(external_id="SPK1", **kwargs) -> SpeakerRecord:
    return SpeakerRecord(external_id=external_id, name=kwargs.pop("name", "Ada Example"), **kwargs)


def talk(external_id="TALK1", **kwargs) -> TalkRecord:
    kwargs.setdefault("speaker_external_ids", ["SPK1"])
    return TalkRecord(external_id=external_id, title=kwargs.pop("title", "A Talk"), **kwargs)


class TestFirstSync:
    def test_creates_speakers_talks_and_links(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker()], talks=[talk()])

        result = sync_program(event)

        assert (result.speakers_created, result.talks_created, result.links_created) == (1, 1, 1)
        assert Speaker.objects.filter(event=event).count() == 1
        assert Talk.objects.get(event=event, external_id="TALK1").speakers.count() == 1

    def test_maps_the_record_fields(self, event, talk_binding, fake_talks):
        fake_talks(
            speakers=[speaker(email="ada@example.org", biography="Bio", avatar_url="https://example.com/a.png")],
            talks=[
                talk(
                    title="Taming Async",
                    description="Desc",
                    duration_minutes=30,
                    session_type="Keynote",
                    state="confirmed",
                    do_not_record=True,
                )
            ],
        )

        sync_program(event)

        t = Talk.objects.get(event=event, external_id="TALK1")
        assert (t.title, t.duration_minutes, t.session_type, t.state) == ("Taming Async", 30, "Keynote", "confirmed")
        assert t.do_not_record is True
        s = Speaker.objects.get(event=event, external_id="SPK1")
        assert (s.email, s.biography) == ("ada@example.org", "Bio")

    def test_scopes_everything_to_the_event(self, event, other_organization, talk_binding, fake_talks):
        from events.models import Event

        other_event = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")
        fake_talks(speakers=[speaker()], talks=[talk()])

        sync_program(event)

        assert Talk.objects.filter(event=other_event).count() == 0


class TestIdempotence:
    def test_re_running_changes_nothing(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker()], talks=[talk()])
        sync_program(event)

        result = sync_program(event)

        assert result.as_counts() == {
            "speakers_created": 0,
            "speakers_updated": 0,
            "talks_created": 0,
            "talks_updated": 0,
            "links_created": 0,
            "links_removed": 0,
            "speakers_absent": 0,
            "talks_absent": 0,
        }

    def test_does_not_touch_updated_at_when_nothing_changed(self, event, talk_binding, fake_talks):
        """An unchanged sync must not look like an edit, or `updated_at` stops meaning anything."""
        fake_talks(speakers=[speaker()], talks=[talk()])
        sync_program(event)
        before = Talk.objects.get(event=event, external_id="TALK1").updated_at

        sync_program(event)

        assert Talk.objects.get(event=event, external_id="TALK1").updated_at == before

    def test_updates_a_changed_title(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker()], talks=[talk(title="Original")])
        sync_program(event)

        fake_talks(speakers=[speaker()], talks=[talk(title="Retitled")])
        result = sync_program(event)

        assert result.talks_updated == 1
        assert Talk.objects.get(event=event, external_id="TALK1").title == "Retitled"

    def test_reuses_rows_rather_than_recreating_them(self, event, talk_binding, fake_talks):
        """The pk must be stable, since review state will hang off it."""
        fake_talks(speakers=[speaker()], talks=[talk()])
        sync_program(event)
        original_pk = Talk.objects.get(event=event, external_id="TALK1").pk

        fake_talks(speakers=[speaker()], talks=[talk(title="Changed")])
        sync_program(event)

        assert Talk.objects.get(event=event, external_id="TALK1").pk == original_pk


class TestNeverDeletesOnAbsence:
    def test_keeps_a_talk_the_provider_stopped_reporting(self, event, talk_binding, fake_talks):
        """The rule that protects review state from a provider glitch or a narrowed credential."""
        fake_talks(speakers=[speaker()], talks=[talk("TALK1"), talk("TALK2", title="Second")])
        sync_program(event)

        fake_talks(speakers=[speaker()], talks=[talk("TALK1")])
        result = sync_program(event)

        assert Talk.objects.filter(event=event).count() == 2
        assert result.talks_absent == 1
        assert result.absent_talk_ids == ["TALK2"]

    def test_keeps_a_speaker_the_provider_stopped_reporting(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker("SPK1"), speaker("SPK2", name="Grace")], talks=[talk()])
        sync_program(event)

        fake_talks(speakers=[speaker("SPK1")], talks=[talk()])
        result = sync_program(event)

        assert Speaker.objects.filter(event=event).count() == 2
        assert result.speakers_absent == 1

    def test_an_empty_provider_response_deletes_nothing(self, event, talk_binding, fake_talks):
        """The worst case: a credential that suddenly sees no events must not empty the database."""
        fake_talks(speakers=[speaker()], talks=[talk()])
        sync_program(event)

        fake_talks(speakers=[], talks=[])
        result = sync_program(event)

        assert Talk.objects.filter(event=event).count() == 1
        assert Speaker.objects.filter(event=event).count() == 1
        assert (result.talks_absent, result.speakers_absent) == (1, 1)


class TestSpeakerLinks:
    def test_adds_a_speaker_added_to_a_talk(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker("SPK1")], talks=[talk(speaker_external_ids=["SPK1"])])
        sync_program(event)

        fake_talks(
            speakers=[speaker("SPK1"), speaker("SPK2", name="Grace")],
            talks=[talk(speaker_external_ids=["SPK1", "SPK2"])],
        )
        result = sync_program(event)

        assert result.links_created == 1
        assert Talk.objects.get(event=event, external_id="TALK1").speakers.count() == 2

    def test_removes_a_speaker_dropped_from_a_talk(self, event, talk_binding, fake_talks):
        """Links are reconciled even though rows are not: a stale link would email the wrong person."""
        fake_talks(
            speakers=[speaker("SPK1"), speaker("SPK2", name="Grace")],
            talks=[talk(speaker_external_ids=["SPK1", "SPK2"])],
        )
        sync_program(event)

        fake_talks(speakers=[speaker("SPK1")], talks=[talk(speaker_external_ids=["SPK1"])])
        result = sync_program(event)

        assert result.links_removed == 1
        assert Talk.objects.get(event=event, external_id="TALK1").speakers.count() == 1
        # The speaker row itself survives, only the link went.
        assert Speaker.objects.filter(event=event, external_id="SPK2").exists()

    def test_skips_a_link_to_an_unknown_speaker_without_failing(self, event, talk_binding, fake_talks):
        """A provider referencing a speaker it did not return should not abort the whole sync."""
        fake_talks(speakers=[speaker("SPK1")], talks=[talk(speaker_external_ids=["SPK1", "GHOST"])])

        result = sync_program(event)

        assert result.links_created == 1
        assert TalkSpeaker.objects.count() == 1

    def test_multi_speaker_talks_keep_every_speaker(self, event, talk_binding, fake_talks):
        fake_talks(
            speakers=[speaker("SPK1"), speaker("SPK2", name="Grace"), speaker("SPK3", name="Alan")],
            talks=[talk(speaker_external_ids=["SPK1", "SPK2", "SPK3"])],
        )

        sync_program(event)

        assert Talk.objects.get(event=event, external_id="TALK1").speakers.count() == 3


class TestSyncRunRecording:
    def test_records_a_successful_run_with_counts(self, event, talk_binding, fake_talks):
        fake_talks(speakers=[speaker()], talks=[talk()])

        sync_program(event)

        run = SyncRun.objects.get(event=event)
        assert run.succeeded is True
        assert run.capability == Capability.TALK_SOURCE
        assert run.provider == "fake"
        assert run.counts["talks_created"] == 1
        assert run.finished_at is not None
        assert run.error == ""

    def test_records_a_failed_run_and_reraises(self, event, talk_binding, fake_talks, monkeypatch):
        from integrations.tests.fakes import FakeTalkSource

        def boom(self):
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(FakeTalkSource, "fetch_speakers", boom)

        with pytest.raises(RuntimeError, match="provider exploded"):
            sync_program(event)

        run = SyncRun.objects.get(event=event)
        assert run.succeeded is False
        assert "provider exploded" in run.error
        assert run.finished_at is not None

    def test_a_failed_sync_persists_nothing(self, event, talk_binding, fake_talks, monkeypatch):
        """Wrapped in a transaction, so a mid-sync failure cannot leave half a program behind."""
        from integrations.tests.fakes import FakeTalkSource

        fake_talks(speakers=[speaker()], talks=[talk()])
        original = FakeTalkSource.fetch_talks

        def explode_after_speakers(self):
            original(self)
            raise RuntimeError("failed partway")

        monkeypatch.setattr(FakeTalkSource, "fetch_talks", explode_after_speakers)

        with pytest.raises(RuntimeError):
            sync_program(event)

        assert Speaker.objects.filter(event=event).count() == 0
        assert Talk.objects.filter(event=event).count() == 0

    def test_records_a_run_even_when_no_provider_is_configured(self, event, fake_providers):
        """No binding at all is an operator error worth a persisted record, not a silent skip."""
        with pytest.raises(IntegrationNotConfigured):
            sync_program(event)

        run = SyncRun.objects.get(event=event)
        assert run.succeeded is False
        assert run.error
