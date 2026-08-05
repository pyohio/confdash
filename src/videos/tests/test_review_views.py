"""The review surface.

The invariant under test throughout: **a speaker reaches their own talks and nothing else**, and the same
screen serves staff reviewing on a speaker's behalf while recording that difference honestly.
"""

import pytest

from accounts.models import User
from events.models import Event, OrganizationMembership
from program.models import Speaker, Talk, TalkSpeaker
from videos.models import Video

pytestmark = pytest.mark.integration


def make_talk_with_video(event, *, title, external_id, speaker_user=None, do_not_record=False):
    talk = Talk.objects.create(event=event, external_id=external_id, title=title, do_not_record=do_not_record)
    if speaker_user is not None:
        speaker = Speaker.objects.create(
            event=event,
            external_id=f"SP-{external_id}",
            name=speaker_user.name or "A Speaker",
            email=speaker_user.email,
            user=speaker_user,
        )
        TalkSpeaker.objects.create(talk=talk, speaker=speaker)
    video = Video.objects.create(event=event, talk=talk, external_id=f"vid-{external_id}", title=title)
    return talk, video


@pytest.fixture
def speaker_user(db) -> User:
    return User.objects.create_user(email="speaker@example.org", name="A Speaker")


@pytest.fixture
def mine(event, speaker_user):
    return make_talk_with_video(event, title="My Talk", external_id="T1", speaker_user=speaker_user)


@pytest.fixture
def theirs(event, db):
    other = User.objects.create_user(email="other@example.org")
    return make_talk_with_video(event, title="Their Talk", external_id="T2", speaker_user=other)


@pytest.fixture
def speaker_client(client, speaker_user):
    """A magic-link-equivalent session: authenticated, with no organizer access."""
    client.force_login(speaker_user)
    return client


class TestTheReviewList:
    def test_shows_only_the_user_s_own_talks(self, speaker_client, mine, theirs):
        response = speaker_client.get("/review/")

        body = response.content.decode()
        assert "My Talk" in body
        assert "Their Talk" not in body

    def test_says_nothing_when_there_is_nothing(self, speaker_client, event, theirs):
        """A user with no linked speaker rows and one whose talks have no videos look the same."""
        response = speaker_client.get("/review/")

        assert response.status_code == 200
        assert "Nothing to review yet" in response.content.decode()

    def test_a_co_presented_talk_appears_once(self, speaker_client, event, speaker_user, mine):
        """The speaker join would otherwise duplicate the row."""
        talk, _ = mine
        co = Speaker.objects.create(event=event, external_id="SP-CO", name="Co", email="co@example.org")
        TalkSpeaker.objects.create(talk=talk, speaker=co)

        response = speaker_client.get("/review/")

        assert response.content.decode().count("My Talk") == 1

    def test_an_anonymous_visitor_is_sent_to_login(self, client, mine):
        response = client.get("/review/")

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


class TestReachingOneVideo:
    def test_a_speaker_can_open_their_own(self, speaker_client, mine):
        _, video = mine

        assert speaker_client.get(f"/review/{video.pk}/").status_code == 200

    def test_a_speaker_cannot_open_someone_else_s(self, speaker_client, mine, theirs):
        _, video = theirs

        assert speaker_client.get(f"/review/{video.pk}/").status_code == 403

    def test_a_missing_video_and_someone_else_s_are_both_refusals(self, speaker_client, mine, theirs):
        """A speaker must not be able to enumerate an event's videos by probing which id errors how.

        These differ (404 against 403) only because a UUIDv7 primary key is not guessable, so there is
        nothing to enumerate. What must never differ is 403-versus-200.
        """
        _, video = theirs
        import uuid

        assert speaker_client.get(f"/review/{video.pk}/").status_code == 403
        assert speaker_client.get(f"/review/{uuid.uuid4()}/").status_code == 404

    def test_a_standalone_video_is_not_a_speaker_s(self, speaker_client, event, mine):
        """No talk means no speaker with a claim to it. Staff review those."""
        standalone = Video.objects.create(event=event, external_id="vid-solo", title="Closing", standalone=True)

        assert speaker_client.get(f"/review/{standalone.pk}/").status_code == 403


class TestApproving:
    def test_a_speaker_s_approval_records_their_consent(self, speaker_client, mine, speaker_user):
        _, video = mine

        speaker_client.post(f"/review/{video.pk}/approve/")

        video.refresh_from_db()
        assert video.review_state == Video.ReviewState.APPROVED
        assert video.approval_source == Video.ApprovalSource.SPEAKER
        assert video.approved_by == speaker_user

    def test_requesting_changes_clears_a_prior_approval(self, speaker_client, mine):
        _, video = mine
        speaker_client.post(f"/review/{video.pk}/approve/")

        speaker_client.post(f"/review/{video.pk}/changes/")

        video.refresh_from_db()
        assert video.review_state == Video.ReviewState.CHANGES_REQUESTED
        assert video.approval_source == ""
        assert video.approved_at is None

    def test_a_speaker_cannot_approve_someone_else_s(self, speaker_client, mine, theirs):
        _, video = theirs

        response = speaker_client.post(f"/review/{video.pk}/approve/")

        video.refresh_from_db()
        assert response.status_code == 403
        assert video.review_state == Video.ReviewState.PENDING

    def test_a_get_cannot_approve(self, speaker_client, mine):
        _, video = mine

        response = speaker_client.get(f"/review/{video.pk}/approve/")

        video.refresh_from_db()
        assert response.status_code == 405
        assert video.review_state == Video.ReviewState.PENDING

    def test_a_do_not_record_talk_offers_no_decision(self, speaker_client, event, speaker_user):
        _, video = make_talk_with_video(
            event, title="Private Talk", external_id="T9", speaker_user=speaker_user, do_not_record=True
        )

        body = speaker_client.get(f"/review/{video.pk}/").content.decode()

        assert "do-not-record" in body
        assert "Approve for publication" not in body


class TestStaffReviewingForASpeaker:
    """Some speakers ask us to do their caption review, so staff use the same screen.

    The difference is recorded rather than hidden: a staff approval is never indistinguishable from the
    speaker having checked it themselves.
    """

    @pytest.fixture
    def staff_client(self, client, organization, as_federated, db):
        organizer = User.objects.create_user(email="staff@example.org")
        OrganizationMembership.objects.create(
            organization=organization, user=organizer, role=OrganizationMembership.Role.OWNER
        )
        return as_federated(client, organizer)

    def test_staff_can_open_a_speaker_s_video(self, staff_client, mine):
        _, video = mine

        assert staff_client.get(f"/review/{video.pk}/").status_code == 200

    def test_the_page_says_it_is_a_staff_review(self, staff_client, mine):
        _, video = mine

        body = staff_client.get(f"/review/{video.pk}/").content.decode()

        assert "as staff" in body

    def test_a_staff_approval_is_recorded_as_such(self, staff_client, mine):
        _, video = mine

        staff_client.post(f"/review/{video.pk}/approve/")

        video.refresh_from_db()
        assert video.review_state == Video.ReviewState.APPROVED
        assert video.approval_source == Video.ApprovalSource.STAFF

    def test_staff_can_review_a_standalone_video(self, staff_client, event):
        """Nothing standalone is stranded unreviewable for lack of a speaker."""
        standalone = Video.objects.create(event=event, external_id="vid-solo", title="Closing", standalone=True)

        staff_client.post(f"/review/{standalone.pk}/approve/")

        standalone.refresh_from_db()
        assert standalone.review_state == Video.ReviewState.APPROVED
        assert standalone.approval_source == Video.ApprovalSource.STAFF

    def test_staff_cannot_reach_another_organization_s_video(self, staff_client, other_organization, db):
        """Organizer review access is org-scoped, not global."""
        elsewhere = Event.objects.create(
            organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC"
        )
        foreign = Video.objects.create(event=elsewhere, external_id="vid-far", title="Elsewhere", standalone=True)

        assert staff_client.get(f"/review/{foreign.pk}/").status_code == 403

    def test_an_unmatched_video_cannot_be_approved(self, staff_client, event):
        """No answer yet to who approved it or on whose behalf."""
        unmatched = Video.objects.create(event=event, external_id="vid-new", title="Something")

        staff_client.post(f"/review/{unmatched.pk}/approve/", follow=True)

        unmatched.refresh_from_db()
        assert unmatched.review_state == Video.ReviewState.PENDING
