"""Matching services and reference normalization."""

import pytest

from videos.models import Video
from videos.services import link_video_to_talk, mark_standalone, normalize_reference, unmatch

pytestmark = pytest.mark.integration


@pytest.fixture
def talk(event):
    from program.models import Talk

    return Talk.objects.create(event=event, external_id="TALK1", title="A Talk", state="confirmed")


@pytest.fixture
def video(event):
    return Video.objects.create(event=event, external_id="vid123", title="A Talk (recording)")


class TestNormalizeReference:
    def test_accepts_a_bare_id_when_no_video_host_is_configured(self, event):
        """The manual path has to work before any OAuth setup, which is its whole reason to exist."""
        assert normalize_reference(event, "dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_strips_surrounding_whitespace(self, event):
        assert normalize_reference(event, "  dQw4w9WgXcQ \n") == "dQw4w9WgXcQ"

    def test_rejects_an_empty_reference(self, event):
        with pytest.raises(ValueError, match="video id or URL"):
            normalize_reference(event, "   ")

    @pytest.mark.parametrize(
        "reference",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "youtu.be/dQw4w9WgXcQ",
            "watch?v=dQw4w9WgXcQ",
            "two words",
        ],
    )
    def test_rejects_a_url_when_no_video_host_can_interpret_it(self, event, reference):
        """Storing an uninterpreted URL as `external_id` would silently duplicate the video.

        `external_id` is unique per event, so a watch URL and the real id are two different rows: the
        first real sync would create a second copy of the same video and nothing would flag it.
        """
        with pytest.raises(ValueError, match="no video host configured"):
            normalize_reference(event, reference)

    def test_delegates_to_the_configured_video_host(self, event, video_binding):
        """Recognizing a watch URL is provider knowledge, so the adapter is asked, not a form.

        The fake's parser handles `v=`, which is enough to show the delegation happened: nothing in
        `videos/` knows what that means.
        """
        assert normalize_reference(event, "https://example.com/watch?v=abc123&t=10") == "abc123"

    def test_a_bare_id_survives_a_configured_video_host(self, event, video_binding):
        assert normalize_reference(event, "abc123") == "abc123"


class TestLinkVideoToTalk:
    def test_links_and_stamps_the_audit_fields(self, video, talk, user):
        link_video_to_talk(video, talk, user=user)

        video.refresh_from_db()
        assert video.talk == talk
        assert video.matched_by == user
        assert video.matched_at is not None

    def test_clears_standalone_when_a_talk_is_chosen(self, video, talk, user):
        mark_standalone(video, user=user)

        link_video_to_talk(video, talk, user=user)

        video.refresh_from_db()
        assert video.standalone is False
        assert video.talk == talk

    def test_refuses_a_talk_from_another_event(self, video, other_organization, user):
        from events.models import Event
        from program.models import Talk

        other_event = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")
        foreign = Talk.objects.create(event=other_event, external_id="OTHER1", title="Elsewhere")

        with pytest.raises(ValueError, match="different event"):
            link_video_to_talk(video, foreign, user=user)

        video.refresh_from_db()
        assert video.talk is None


class TestMarkStandalone:
    def test_settles_a_video_with_no_talk(self, video, user):
        mark_standalone(video, user=user)

        video.refresh_from_db()
        assert video.standalone is True
        assert video.talk is None
        assert video.needs_matching is False
        assert video.matched_by == user

    def test_drops_an_existing_talk_link(self, video, talk, user):
        link_video_to_talk(video, talk, user=user)

        mark_standalone(video, user=user)

        video.refresh_from_db()
        assert video.talk is None
        assert video.standalone is True


class TestUnmatch:
    def test_returns_a_video_to_the_queue(self, video, talk, user):
        link_video_to_talk(video, talk, user=user)

        unmatch(video)

        video.refresh_from_db()
        assert video.needs_matching is True
        assert video.talk is None

    def test_clears_the_audit_stamps(self, video, talk, user):
        """Leaving them would claim someone decided the current state, which is now undecided."""
        link_video_to_talk(video, talk, user=user)

        unmatch(video)

        video.refresh_from_db()
        assert video.matched_by is None
        assert video.matched_at is None

    def test_also_clears_standalone(self, video, user):
        mark_standalone(video, user=user)

        unmatch(video)

        video.refresh_from_db()
        assert video.standalone is False
        assert video.needs_matching is True


class TestApprove:
    """`approval_source` is derived, so no caller can record a staff approval as a speaker's."""

    def test_a_speaker_approving_their_own_talk_records_speaker_consent(self, event, user):
        from program.models import Speaker, Talk, TalkSpeaker
        from videos.services import approve

        t = Talk.objects.create(event=event, external_id="T1", title="Talk", state="confirmed")
        s = Speaker.objects.create(event=event, external_id="S1", name="A Speaker", user=user)
        TalkSpeaker.objects.create(talk=t, speaker=s)
        v = Video.objects.create(event=event, external_id="v1", talk=t)

        approve(v, user=user)

        v.refresh_from_db()
        assert v.approval_source == Video.ApprovalSource.SPEAKER
        assert v.approved_by == user
        assert v.review_state == Video.ReviewState.APPROVED

    def test_staff_approving_someone_elses_talk_records_staff(self, video, talk, user):
        """Doing a speaker's caption review for them must not look like the speaker checked it."""
        from videos.services import approve

        link_video_to_talk(video, talk, user=user)

        approve(video, user=user)

        video.refresh_from_db()
        assert video.approval_source == Video.ApprovalSource.STAFF
        assert video.may_be_published is True

    def test_staff_approving_a_standalone_video_records_staff(self, video, user):
        from videos.services import approve

        mark_standalone(video, user=user)

        approve(video, user=user)

        video.refresh_from_db()
        assert video.approval_source == Video.ApprovalSource.STAFF
        assert video.may_be_published is True

    def test_refuses_to_approve_a_video_still_awaiting_matching(self, video, user):
        """Approving it would produce a record that can never publish and cannot say who consented."""
        from videos.services import approve

        with pytest.raises(ValueError, match="before approving"):
            approve(video, user=user)

        video.refresh_from_db()
        assert video.review_state == Video.ReviewState.PENDING


class TestRequestChanges:
    def test_clears_a_prior_approval(self, video, talk, user):
        """Otherwise a stale approval could still satisfy the publication guard."""
        from videos.services import approve, request_changes

        link_video_to_talk(video, talk, user=user)
        approve(video, user=user)

        request_changes(video, user=user)

        video.refresh_from_db()
        assert video.review_state == Video.ReviewState.CHANGES_REQUESTED
        assert video.approval_source == ""
        assert video.approved_by is None
        assert video.approved_at is None
        assert video.may_be_published is False
