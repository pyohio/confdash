"""The organizer confirm queue.

Exercised through the real URL, template, and session rather than by calling views directly: the point of
this screen is that a path carries the tenant, a decorator authorizes it, and a POST records a decision.
Calling the view function would skip most of that.
"""

import pytest

from accounts.auth_method import AuthMethod
from events.models import Event, OrganizationMembership
from events.scopes import Scope
from program.models import Talk
from videos.models import Video

pytestmark = pytest.mark.integration


@pytest.fixture
def talks(event):
    """Titles taken from the real 2025 programme, including the pair that needs a margin check."""
    return [
        Talk.objects.create(event=event, external_id="T1", title="Deploy Django: GitOps Kubernetes Made Easy"),
        Talk.objects.create(event=event, external_id="T2", title="Organizing and Maintaining Your Code-Scape"),
        Talk.objects.create(event=event, external_id="T3", title="Lights, Python, Action!"),
    ]


def make_video(event, title, external_id):
    return Video.objects.create(event=event, external_id=external_id, title=title)


@pytest.fixture
def queue(event, talks):
    """One clear match, one non-talk, one ambiguous-by-truncation."""
    return {
        "clear": make_video(event, "Deploy_Django_GitOps_Kubernetes_Made_Easy.mp4", "vid-clear"),
        "nontalk": make_video(event, "sunday_welcome.mp4", "vid-welcome"),
        "partial": make_video(event, "Lights_Python.mp4", "vid-partial"),
    }


def url(event, suffix=""):
    return f"/o/{event.organization.slug}/{event.slug}/videos/{suffix}"


class TestTheQueuePage:
    def test_lists_videos_awaiting_a_decision(self, organizer_client, event, queue):
        response = organizer_client.get(url(event))

        assert response.status_code == 200
        body = response.content.decode()
        assert "Deploy_Django_GitOps_Kubernetes_Made_Easy.mp4" in body
        assert "sunday_welcome.mp4" in body

    def test_offers_the_right_talk_first(self, organizer_client, event, queue):
        response = organizer_client.get(url(event))

        rows = {row["video"].external_id: row for row in response.context["rows"]}
        assert rows["vid-clear"]["suggestions"][0].talk.title == "Deploy Django: GitOps Kubernetes Made Easy"
        assert rows["vid-clear"]["is_unambiguous"] is True

    def test_a_non_talk_gets_no_suggestion_rather_than_a_weak_one(self, organizer_client, event, queue):
        response = organizer_client.get(url(event))

        rows = {row["video"].external_id: row for row in response.context["rows"]}
        assert rows["vid-welcome"]["suggestions"] == []
        assert rows["vid-welcome"]["is_unambiguous"] is False

    def test_excludes_videos_already_settled(self, organizer_client, event, queue, talks):
        """Both kinds of settled: matched, and deliberately standalone."""
        queue["clear"].talk = talks[0]
        queue["clear"].save()
        queue["nontalk"].standalone = True
        queue["nontalk"].save()

        response = organizer_client.get(url(event))

        assert [row["video"].external_id for row in response.context["rows"]] == ["vid-partial"]
        assert {v.external_id for v in response.context["settled"]} == {"vid-clear", "vid-welcome"}

    def test_says_so_when_nothing_is_left(self, organizer_client, event, talks):
        response = organizer_client.get(url(event))

        assert "Nothing left to decide" in response.content.decode()

    def test_only_this_event_s_videos_appear(self, organizer_client, event, queue, other_organization):
        """The queue is scoped by the event in the path, not by everything the organizer can reach."""
        elsewhere = Event.objects.create(
            organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC"
        )
        make_video(elsewhere, "Someone_Elses_Talk.mp4", "vid-other")

        response = organizer_client.get(url(event))

        assert "Someone_Elses_Talk.mp4" not in response.content.decode()


class TestConfirming:
    def test_a_match_records_who_made_it(self, organizer_client, event, queue, talks, organizer):
        video = queue["clear"]

        response = organizer_client.post(url(event, f"{video.pk}/match/"), {"talk": talks[0].pk})

        video.refresh_from_db()
        assert response.status_code == 200
        assert video.talk == talks[0]
        assert video.matched_by == organizer
        assert video.matched_at is not None

    def test_the_response_is_just_the_replaced_row(self, organizer_client, event, queue, talks):
        """An HTMX swap, so the rest of the queue and the scroll position survive."""
        video = queue["clear"]

        response = organizer_client.post(url(event, f"{video.pk}/match/"), {"talk": talks[0].pk})

        body = response.content.decode()
        assert f'id="video-{video.pk}"' in body
        assert "<html" not in body
        # The other queued videos must not be in the fragment.
        assert "sunday_welcome.mp4" not in body

    def test_marking_standalone_settles_it_without_a_talk(self, organizer_client, event, queue, organizer):
        video = queue["nontalk"]

        organizer_client.post(url(event, f"{video.pk}/standalone/"))

        video.refresh_from_db()
        assert video.standalone is True
        assert video.talk is None
        assert video.review_track == "staff"
        assert video.matched_by == organizer

    def test_undo_returns_a_video_to_the_queue(self, organizer_client, event, queue, talks):
        video = queue["clear"]
        organizer_client.post(url(event, f"{video.pk}/match/"), {"talk": talks[0].pk})

        response = organizer_client.post(url(event, f"{video.pk}/undo/"))

        video.refresh_from_db()
        assert video.needs_matching is True
        # The audit stamps go too: leaving them would claim someone decided the current state.
        assert video.matched_by is None
        assert video.matched_at is None
        # And it comes back as a queue row, suggestions and all.
        assert "Match" in response.content.decode()

    def test_a_talk_from_another_event_is_refused(self, organizer_client, event, queue, other_organization):
        """Cross-event matching would attach one event's video to another's talk."""
        elsewhere = Event.objects.create(
            organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC"
        )
        foreign_talk = Talk.objects.create(event=elsewhere, external_id="X1", title="Not Ours")

        response = organizer_client.post(url(event, f"{queue['clear'].pk}/match/"), {"talk": foreign_talk.pk})

        queue["clear"].refresh_from_db()
        assert response.status_code == 404
        assert queue["clear"].talk is None

    def test_a_video_from_another_event_is_refused(self, organizer_client, event, other_organization, talks):
        elsewhere = Event.objects.create(
            organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC"
        )
        foreign_video = make_video(elsewhere, "Not Ours.mp4", "vid-other")

        response = organizer_client.post(url(event, f"{foreign_video.pk}/match/"), {"talk": talks[0].pk})

        foreign_video.refresh_from_db()
        assert response.status_code == 404
        assert foreign_video.talk is None

    def test_a_get_cannot_change_anything(self, organizer_client, event, queue, talks):
        """Mutations are POST-only, so a prefetched or crawled URL cannot settle a video."""
        response = organizer_client.get(url(event, f"{queue['clear'].pk}/standalone/"))

        queue["clear"].refresh_from_db()
        assert response.status_code == 405
        assert queue["clear"].standalone is False

    def test_authorization_is_checked_before_the_method(self, client, event, queue):
        """An outsider gets 403, not the 405 that would confirm the endpoint exists."""
        assert client.get(url(event, f"{queue['clear'].pk}/standalone/")).status_code == 403


class TestBulkAccept:
    def test_accepts_only_the_unambiguous_ones(self, organizer_client, event, queue, talks):
        """A bulk action that guesses is worse than one that does less."""
        response = organizer_client.post(url(event, "accept-suggested/"))

        assert response.status_code == 302
        queue["clear"].refresh_from_db()
        queue["nontalk"].refresh_from_db()
        assert queue["clear"].talk == talks[0]
        assert queue["nontalk"].talk is None
        assert queue["nontalk"].standalone is False

    def test_leaves_a_near_tie_alone(self, organizer_client, event):
        """Two talks in a series can both clear the confidence threshold; picking one would be a toss."""
        Talk.objects.create(event=event, external_id="P1", title="Async Python: Part One")
        Talk.objects.create(event=event, external_id="P2", title="Async Python: Part Two")
        video = make_video(event, "Async Python Part.mp4", "vid-series")

        organizer_client.post(url(event, "accept-suggested/"))

        video.refresh_from_db()
        assert video.talk is None

    def test_reports_what_it_did(self, organizer_client, event, queue, talks):
        response = organizer_client.post(url(event, "accept-suggested/"), follow=True)

        assert "Matched 1 video(s)" in response.content.decode()


class TestAuthorization:
    def test_a_non_member_cannot_reach_the_queue(self, client, event, user, as_federated):
        as_federated(client, user)

        assert client.get(url(event)).status_code == 403

    def test_a_magic_link_session_cannot_reach_the_queue(self, client, event, organizer, as_federated):
        """Even with a real membership: otherwise the membership row bypasses the org's IdP."""
        as_federated(client, organizer, method=AuthMethod.MAGIC_LINK)

        assert client.get(url(event)).status_code == 403

    def test_an_organizer_without_the_videos_scope_is_refused(self, client, event, organization, as_federated, db):
        from accounts.models import User

        comms_only = User.objects.create_user(email="comms@example.org")
        OrganizationMembership.objects.create(
            organization=organization,
            user=comms_only,
            role=OrganizationMembership.Role.ORGANIZER,
            scopes=[Scope.COMMS],
        )
        as_federated(client, comms_only)

        assert client.get(url(event)).status_code == 403

    def test_mutations_are_authorized_too_not_only_the_page(self, client, event, queue, talks, user, as_federated):
        """A read check on the page is worthless if the POST endpoints are open."""
        as_federated(client, user)

        response = client.post(url(event, f"{queue['clear'].pk}/match/"), {"talk": talks[0].pk})

        queue["clear"].refresh_from_db()
        assert response.status_code == 403
        assert queue["clear"].talk is None

    def test_an_anonymous_request_is_refused(self, client, event):
        assert client.get(url(event)).status_code == 403
