"""Session authentication-mechanism recording.

No database: these exercise the session dict and the allow-list, so the "user" is a stub with just
the attribute the allow-list reads.
"""

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from accounts.auth_method import (
    SESSION_KEY,
    AuthMethod,
    get_auth_method,
    permits_organizer_access,
    set_auth_method,
)

pytestmark = pytest.mark.unit


class StubUser:
    def __init__(self, *, is_superuser: bool = False):
        self.is_authenticated = True
        self.is_superuser = is_superuser


def make_request(*, method: AuthMethod | str | None = None, is_superuser: bool = False):
    request = RequestFactory().get("/")
    # Never saved, so this stays in memory and needs no database.
    request.session = SessionStore()
    request.user = StubUser(is_superuser=is_superuser)
    if method is not None:
        request.session[SESSION_KEY] = str(method)
    return request


class TestRecording:
    def test_round_trips_the_method(self):
        request = make_request()
        set_auth_method(request, AuthMethod.FEDERATED)
        assert get_auth_method(request) == AuthMethod.FEDERATED

    def test_stores_a_plain_string(self):
        """Session data is serialized, so an enum member must not be written as one."""
        request = make_request()
        set_auth_method(request, AuthMethod.MAGIC_LINK)
        assert request.session[SESSION_KEY] == "magic_link"
        assert type(request.session[SESSION_KEY]) is str

    def test_unset_is_none(self):
        assert get_auth_method(make_request()) is None

    def test_unrecognized_value_is_none_rather_than_raising(self):
        """A value left by a removed mechanism must not raise on every request."""
        assert get_auth_method(make_request(method="carrier_pigeon")) is None


class TestOrganizerAllowList:
    def test_federated_sessions_are_permitted(self):
        assert permits_organizer_access(make_request(method=AuthMethod.FEDERATED)) is True

    def test_magic_link_sessions_are_refused(self):
        """The rule that keeps a membership row from becoming a path around the org's IdP."""
        assert permits_organizer_access(make_request(method=AuthMethod.MAGIC_LINK)) is False

    def test_password_sessions_are_permitted_only_for_superusers(self):
        assert permits_organizer_access(make_request(method=AuthMethod.PASSWORD, is_superuser=True)) is True
        assert permits_organizer_access(make_request(method=AuthMethod.PASSWORD)) is False

    def test_a_session_with_no_recorded_method_is_refused(self):
        """Fails closed. An unrecorded mechanism is an unknown one."""
        assert permits_organizer_access(make_request()) is False

    def test_an_unknown_method_is_refused(self):
        """The allow-list is why: a new mechanism gets no organizer access until granted it."""
        assert permits_organizer_access(make_request(method="carrier_pigeon")) is False
