"""The organizer view decorator, and the URL shape it depends on.

Organizer URLs are `/o/<organization_slug>/<event_slug>/...`, so the tenant is a routing concern and the
decorator can refuse before a view body runs. These tests exercise it through real URL resolution rather
than by calling the wrapped function, because the thing being tested is that the two slugs in the path
become an authorization decision.
"""

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.test import RequestFactory
from django.urls import path

from accounts.auth_method import AuthMethod, set_auth_method
from events.decorators import has_scope, organizer_view, require_scope
from events.models import Event, OrganizationMembership
from events.scopes import Scope

pytestmark = pytest.mark.integration


@organizer_view(Scope.VIDEOS)
def videos_view(request, event):
    """Stands in for a real organizer screen; returns what it was handed so tests can check it."""
    return HttpResponse(f"{event.organization.slug}/{event.slug}")


@organizer_view(Scope.SPONSORSHIP)
def sponsorship_view(request, event):
    return HttpResponse("sponsorship")


urlpatterns = [
    path("o/<slug:organization_slug>/<slug:event_slug>/videos/", videos_view),
    path("o/<slug:organization_slug>/<slug:event_slug>/sponsors/", sponsorship_view),
]


def make_request(user, *, method: AuthMethod | None = AuthMethod.FEDERATED):
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.user = user
    if method is not None:
        set_auth_method(request, method)
    return request


@pytest.fixture
def videos_organizer(organization, db):
    """An organizer restricted to `videos`, to prove scopes are actually consulted."""
    from accounts.models import User

    user = User.objects.create_user(email="video-person@example.org")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.ORGANIZER,
        scopes=[Scope.VIDEOS],
    )
    return user


class TestResolution:
    def test_hands_the_view_the_event_the_url_names(self, event, organizer):
        response = videos_view(make_request(organizer), organization_slug="pyohio", event_slug="2026")

        assert response.content == b"pyohio/2026"

    def test_the_slug_pair_is_matched_together(self, event, organizer, other_organization):
        """`2026` exists in every organization, so an event slug alone must never resolve one.

        Looking up by event slug and checking the organization afterwards would serve another tenant's
        event to anyone who guessed a slug, which is the failure this pairing prevents.
        """
        Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC")

        with pytest.raises(PermissionDenied):
            videos_view(make_request(organizer), organization_slug="otherconf", event_slug="2026")

    def test_an_unknown_event_is_a_404(self, event, organizer):
        with pytest.raises(Http404):
            videos_view(make_request(organizer), organization_slug="pyohio", event_slug="1999")

    def test_an_unknown_organization_is_a_404(self, event, organizer):
        with pytest.raises(Http404):
            videos_view(make_request(organizer), organization_slug="nope", event_slug="2026")


class TestAuthorization:
    def test_a_non_member_is_refused(self, event, user):
        """`user` has no membership anywhere."""
        with pytest.raises(PermissionDenied):
            videos_view(make_request(user), organization_slug="pyohio", event_slug="2026")

    def test_a_magic_link_session_is_refused(self, event, organizer):
        """The invariant that keeps a membership row from becoming a way around the org's IdP."""
        request = make_request(organizer, method=AuthMethod.MAGIC_LINK)

        with pytest.raises(PermissionDenied):
            videos_view(request, organization_slug="pyohio", event_slug="2026")

    def test_a_session_with_no_recorded_method_is_refused(self, event, organizer):
        """Fails closed: a login path that forgot `set_auth_method` grants nothing."""
        request = make_request(organizer, method=None)

        with pytest.raises(PermissionDenied):
            videos_view(request, organization_slug="pyohio", event_slug="2026")

    def test_the_scope_is_the_one_the_decorator_names(self, event, videos_organizer):
        """Same user, same event, same session: only the required scope differs."""
        request = make_request(videos_organizer)

        assert videos_view(request, organization_slug="pyohio", event_slug="2026").status_code == 200
        with pytest.raises(PermissionDenied):
            sponsorship_view(request, organization_slug="pyohio", event_slug="2026")

    def test_membership_elsewhere_grants_nothing_here(self, event, other_organization, db):
        """Cross-organization access must be impossible, and cannot be a database constraint."""
        from accounts.models import User

        outsider = User.objects.create_user(email="other@example.org")
        OrganizationMembership.objects.create(
            organization=other_organization,
            user=outsider,
            role=OrganizationMembership.Role.OWNER,
        )

        with pytest.raises(PermissionDenied):
            videos_view(make_request(outsider), organization_slug="pyohio", event_slug="2026")


class TestRouting:
    """Through the URL resolver, so the path shape itself is covered."""

    def test_the_path_shape_reaches_the_view(self, client, event, organizer, settings):
        settings.ROOT_URLCONF = __name__
        client.force_login(organizer)
        session = client.session
        set_auth_method_on(session, AuthMethod.FEDERATED)

        response = client.get("/o/pyohio/2026/videos/")

        assert response.status_code == 200
        assert response.content == b"pyohio/2026"

    def test_an_unauthorized_request_gets_403_not_a_redirect(self, client, event, user, settings):
        """A 302 to a login page would be wrong: the user *is* logged in, just not for this."""
        settings.ROOT_URLCONF = __name__
        client.force_login(user)
        session = client.session
        set_auth_method_on(session, AuthMethod.FEDERATED)

        assert client.get("/o/pyohio/2026/videos/").status_code == 403

    def test_an_anonymous_request_is_refused(self, client, event, settings):
        settings.ROOT_URLCONF = __name__

        assert client.get("/o/pyohio/2026/videos/").status_code == 403


def set_auth_method_on(session, method: AuthMethod) -> None:
    """Set the mechanism on a test client's session, which is a store rather than a request."""
    from accounts.auth_method import SESSION_KEY

    session[SESSION_KEY] = str(method)
    session.save()


class TestInViewHelpers:
    def test_require_scope_checks_a_second_scope(self, event, videos_organizer):
        request = make_request(videos_organizer)

        require_scope(request, event, Scope.VIDEOS)
        with pytest.raises(PermissionDenied):
            require_scope(request, event, Scope.COMMS)

    def test_has_scope_answers_without_raising(self, event, videos_organizer):
        """For templates: a button an organizer cannot use should not be rendered."""
        request = make_request(videos_organizer)

        assert has_scope(request, event, Scope.VIDEOS) is True
        assert has_scope(request, event, Scope.COMMS) is False
