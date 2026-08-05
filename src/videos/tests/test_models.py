"""Video model rules.

The matching states and the publication guard get the coverage, since both are places where being
wrong has visible consequences: a video shown to the wrong speaker, or one published that should not
have been.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from videos.models import Video

pytestmark = pytest.mark.integration


@pytest.fixture
def talk(event):
    from program.models import Talk

    return Talk.objects.create(event=event, external_id="TALK1", title="A Talk", state="confirmed")


@pytest.fixture
def video(event):
    return Video.objects.create(event=event, external_id="vid123", title="A Talk (recording)")


class TestMatchingStates:
    def test_a_new_video_needs_matching(self, video):
        assert video.needs_matching is True
        assert video.is_matched is False

    def test_a_linked_video_is_matched(self, video, talk):
        video.talk = talk
        assert video.is_matched is True
        assert video.needs_matching is False

    def test_a_standalone_video_no_longer_needs_matching(self, video):
        """The distinction a nullable FK cannot express: settled versus not yet looked at."""
        video.standalone = True
        assert video.needs_matching is False
        assert video.is_matched is False

    def test_the_database_rejects_standalone_with_a_talk(self, video, talk):
        video.talk = talk
        video.standalone = True
        with pytest.raises(IntegrityError), transaction.atomic():
            video.save()

    def test_clean_rejects_standalone_with_a_talk(self, video, talk):
        video.talk = talk
        video.standalone = True
        with pytest.raises(ValidationError, match="standalone"):
            video.full_clean()


class TestEventScoping:
    def test_clean_rejects_a_talk_from_another_event(self, video, other_organization):
        """Cross-event matching cannot be a DB constraint without denormalizing, so it needs its test."""
        from events.models import Event
        from program.models import Talk

        other_event = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")
        foreign_talk = Talk.objects.create(event=other_event, external_id="OTHER1", title="Elsewhere")

        video.talk = foreign_talk

        with pytest.raises(ValidationError, match="different event"):
            video.full_clean()

    def test_external_id_is_unique_per_event_not_globally(self, video, other_organization):
        from events.models import Event

        other_event = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")

        twin = Video.objects.create(event=other_event, external_id=video.external_id)

        assert twin.external_id == video.external_id

    def test_the_same_external_id_twice_in_one_event_is_rejected(self, video, event):
        with pytest.raises(IntegrityError), transaction.atomic():
            Video.objects.create(event=event, external_id=video.external_id)


class TestReviewTrack:
    """Who reviews a video follows from the matching outcome, so it is derived rather than stored."""

    def test_an_unmatched_video_has_no_track_yet(self, video):
        assert video.review_track is None

    def test_a_matched_video_goes_to_its_speakers(self, video, talk):
        video.talk = talk
        assert video.review_track == "speaker"

    def test_a_standalone_video_goes_to_staff(self, video):
        """A welcome or closing remarks has no speaker to ask, so staff own the review."""
        video.standalone = True
        assert video.review_track == "staff"


class TestPublicationGuard:
    def test_an_unapproved_video_may_not_be_published(self, video, talk):
        video.talk = talk
        assert video.may_be_published is False

    def test_an_approved_matched_video_may_be_published(self, video, talk):
        video.talk = talk
        video.review_state = Video.ReviewState.APPROVED
        assert video.may_be_published is True

    def test_an_approved_standalone_video_may_be_published(self, video):
        """Staff approval is a real approval: standalone videos must not be stranded unpublishable."""
        video.standalone = True
        video.review_state = Video.ReviewState.APPROVED

        assert video.may_be_published is True

    def test_an_approved_video_still_awaiting_matching_may_not_be_published(self, video):
        """Approved but undecided: there is no answer to who approved it or on whose behalf."""
        video.review_state = Video.ReviewState.APPROVED

        assert video.needs_matching is True
        assert video.may_be_published is False

    def test_do_not_record_blocks_publication_even_when_approved(self, video, talk):
        """The guard that overrides everything, including a speaker's own approval."""
        talk.do_not_record = True
        talk.save()
        video.talk = talk
        video.review_state = Video.ReviewState.APPROVED

        assert video.may_be_published is False

    def test_defaults_are_the_safe_ones(self, video):
        assert video.review_state == Video.ReviewState.PENDING
        assert video.publication_state == Video.PublicationState.UNPUBLISHED
        assert video.privacy_status == Video.PrivacyStatus.PRIVATE
