"""Who may review a video.

The three rules that matter: a speaker reaches their own talks and nothing else, an organizer with the
videos scope reaches everything in their organization, and holding a membership is not enough on its own
if you arrived on a magic link.
"""

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from accounts.auth_method import AuthMethod, set_auth_method
from accounts.models import User
from events.models import OrganizationMembership
from events.scopes import Scope
from program.models import Speaker, Talk, TalkSpeaker
from videos.authz import (
    is_speaker_on,
    may_review,
    require_review_access,
    reviewable_videos,
    speaker_videos,
)
from videos.models import Video

pytestmark = pytest.mark.integration


def make_request(user, *, method: AuthMethod):
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.user = user
    set_auth_method(request, method)
    return request


@pytest.fixture
def speaker_user(db):
    return User.objects.create_user(email="speaker@example.org", name="A Speaker")


@pytest.fixture
def other_speaker_user(db):
    return User.objects.create_user(email="other@example.org", name="Another Speaker")


@pytest.fixture
def own_talk(event, speaker_user):
    talk = Talk.objects.create(event=event, external_id="MINE", title="My Talk", state="confirmed")
    speaker = Speaker.objects.create(event=event, external_id="SPKME", name="A Speaker", user=speaker_user)
    TalkSpeaker.objects.create(talk=talk, speaker=speaker)
    return talk


@pytest.fixture
def someone_elses_talk(event, other_speaker_user):
    talk = Talk.objects.create(event=event, external_id="THEIRS", title="Their Talk", state="confirmed")
    speaker = Speaker.objects.create(event=event, external_id="SPKTHEM", name="Another", user=other_speaker_user)
    TalkSpeaker.objects.create(talk=talk, speaker=speaker)
    return talk


@pytest.fixture
def own_video(event, own_talk):
    return Video.objects.create(event=event, external_id="mine1", title="My recording", talk=own_talk)


@pytest.fixture
def foreign_video(event, someone_elses_talk):
    return Video.objects.create(event=event, external_id="theirs1", title="Their recording", talk=someone_elses_talk)


@pytest.fixture
def standalone_video(event):
    return Video.objects.create(event=event, external_id="welcome1", title="Welcome", standalone=True)


@pytest.fixture
def video_organizer(organization, db):
    user = User.objects.create_user(email="organizer-video@example.org")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.ORGANIZER,
    )
    return user


class TestSpeakerAccess:
    def test_a_speaker_reaches_their_own_video(self, speaker_user, own_video):
        request = make_request(speaker_user, method=AuthMethod.MAGIC_LINK)
        assert may_review(request, own_video) is True

    def test_a_speaker_cannot_reach_another_speakers_video(self, speaker_user, foreign_video):
        request = make_request(speaker_user, method=AuthMethod.MAGIC_LINK)
        assert may_review(request, foreign_video) is False

    def test_a_speaker_cannot_reach_a_standalone_video(self, speaker_user, standalone_video):
        """No talk means no speaker with a claim; those are staff-reviewed."""
        request = make_request(speaker_user, method=AuthMethod.MAGIC_LINK)
        assert may_review(request, standalone_video) is False

    def test_speaker_videos_lists_only_their_own(self, speaker_user, own_video, foreign_video, standalone_video):
        assert list(speaker_videos(speaker_user)) == [own_video]

    def test_a_co_presented_talk_appears_once(self, event, speaker_user, own_talk, own_video):
        """The speaker join would otherwise duplicate the row per speaker on the talk."""
        second = Speaker.objects.create(event=event, external_id="SPK2", name="Co-presenter")
        TalkSpeaker.objects.create(talk=own_talk, speaker=second)

        assert list(speaker_videos(speaker_user)) == [own_video]

    def test_an_unlinked_speaker_row_grants_nothing(self, event, own_talk):
        """A Speaker with no `user` yet must not match anyone."""
        stranger = User.objects.create_user(email="stranger@example.org")
        assert list(speaker_videos(stranger)) == []

    def test_anonymous_users_get_nothing(self, own_video):
        from django.contrib.auth.models import AnonymousUser

        assert list(speaker_videos(AnonymousUser())) == []
        assert is_speaker_on(AnonymousUser(), own_video) is False


class TestOrganizerAccess:
    def test_an_organizer_reaches_every_video_in_the_event(
        self, video_organizer, own_video, foreign_video, standalone_video
    ):
        request = make_request(video_organizer, method=AuthMethod.FEDERATED)

        for video in (own_video, foreign_video, standalone_video):
            assert may_review(request, video) is True

    def test_reviewable_videos_returns_everything_for_an_organizer(
        self, video_organizer, event, own_video, foreign_video, standalone_video
    ):
        request = make_request(video_organizer, method=AuthMethod.FEDERATED)

        assert reviewable_videos(request, event).count() == 3

    def test_reviewable_videos_returns_only_their_own_for_a_speaker(
        self, speaker_user, event, own_video, foreign_video, standalone_video
    ):
        request = make_request(speaker_user, method=AuthMethod.MAGIC_LINK)

        assert list(reviewable_videos(request, event)) == [own_video]

    def test_a_restricted_membership_without_the_videos_scope_is_refused(
        self, video_organizer, organization, own_video, foreign_video
    ):
        membership = OrganizationMembership.objects.get(user=video_organizer, organization=organization)
        membership.scopes = [Scope.SPONSORSHIP]
        membership.save()

        request = make_request(video_organizer, method=AuthMethod.FEDERATED)

        assert may_review(request, foreign_video) is False

    def test_an_organizer_on_a_magic_link_gets_only_their_own_talks(
        self, speaker_user, organization, own_video, foreign_video
    ):
        """The rule that stops a membership row becoming a way around the organization's IdP.

        This user is both an organizer and a speaker. On a magic link they are only a speaker.
        """
        OrganizationMembership.objects.create(
            organization=organization,
            user=speaker_user,
            role=OrganizationMembership.Role.ORGANIZER,
        )
        request = make_request(speaker_user, method=AuthMethod.MAGIC_LINK)

        assert may_review(request, own_video) is True
        assert may_review(request, foreign_video) is False

    def test_the_same_user_federated_reaches_everything(self, speaker_user, organization, own_video, foreign_video):
        OrganizationMembership.objects.create(
            organization=organization,
            user=speaker_user,
            role=OrganizationMembership.Role.ORGANIZER,
        )
        request = make_request(speaker_user, method=AuthMethod.FEDERATED)

        assert may_review(request, foreign_video) is True


class TestCrossOrganization:
    def test_an_organizer_cannot_reach_another_organizations_video(self, video_organizer, other_organization):
        from events.models import Event

        other_event = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")
        foreign = Video.objects.create(event=other_event, external_id="elsewhere1", standalone=True)

        request = make_request(video_organizer, method=AuthMethod.FEDERATED)

        assert may_review(request, foreign) is False


class TestRequireReviewAccess:
    def test_passes_when_permitted(self, speaker_user, own_video):
        require_review_access(make_request(speaker_user, method=AuthMethod.MAGIC_LINK), own_video)

    def test_raises_permission_denied_when_not(self, speaker_user, foreign_video):
        with pytest.raises(PermissionDenied):
            require_review_access(make_request(speaker_user, method=AuthMethod.MAGIC_LINK), foreign_video)
