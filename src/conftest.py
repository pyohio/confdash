"""Shared pytest fixtures.

Plain factory functions rather than a factory library: fixtures call the same service-layer and
model code the application does, so a test that passes proves the real path works.
"""

import pytest

from accounts.models import User
from events.models import Event, Organization, OrganizationMembership
from integrations import registry
from integrations.models import EventProviderBinding, ProviderConnection
from integrations.providers.base import Capability
from integrations.tests.fakes import FakeTalkSource, FakeVideoHost


@pytest.fixture
def organization(db) -> Organization:
    return Organization.objects.create(slug="pyohio", name="PyOhio")


@pytest.fixture
def other_organization(db) -> Organization:
    """A second tenant, for checking that scoping actually holds."""
    return Organization.objects.create(slug="otherconf", name="OtherConf")


@pytest.fixture
def event(organization) -> Event:
    return Event.objects.create(
        organization=organization,
        slug="2026",
        name="PyOhio 2026",
        series="pyohio",
        timezone="America/New_York",
    )


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(email="organizer@example.org", name="An Organizer")


@pytest.fixture
def organizer(organization, user) -> User:
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.ORGANIZER,
    )
    return user


@pytest.fixture
def as_federated():
    """Log a test client in with a recorded authentication mechanism.

    `force_login` alone is not enough to reach an organizer view: `accounts.auth_method` requires the
    session to say *how* it authenticated, and a session with nothing recorded fails closed. That is the
    invariant, so tests have to satisfy it the same way a real login path does.
    """
    from accounts.auth_method import SESSION_KEY, AuthMethod

    def login(client, user, *, method: AuthMethod = AuthMethod.FEDERATED):
        client.force_login(user)
        session = client.session
        session[SESSION_KEY] = str(method)
        session.save()
        return client

    return login


@pytest.fixture
def organizer_client(client, organizer, as_federated):
    """A client authenticated as an unrestricted organizer of `organization`."""
    return as_federated(client, organizer)


# --- Providers --------------------------------------------------------------
#
# Shared rather than scoped to `integrations/tests/` because sync services live in the apps that own
# the data they persist, and those are what the fakes exist to test.


@pytest.fixture
def fake_providers():
    """Register the fake adapters for the duration of one test.

    The registry is module-level state, so it is snapshotted and restored rather than cleared: real
    adapters registered at app startup must still be there afterwards.
    """
    saved = dict(registry._REGISTRY)
    registry.register(FakeTalkSource)
    registry.register(FakeVideoHost)
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


@pytest.fixture
def talk_connection(organization, fake_providers) -> ProviderConnection:
    connection = ProviderConnection(
        organization=organization,
        slug="fake-talks-2026",
        name="Fake talk source (2026)",
        capability=Capability.TALK_SOURCE,
        provider="fake",
    )
    connection.set_credentials({"api_token": "test-token"})
    connection.save()
    return connection


@pytest.fixture
def talk_binding(event, talk_connection) -> EventProviderBinding:
    return EventProviderBinding.objects.create(
        event=event,
        capability=Capability.TALK_SOURCE,
        connection=talk_connection,
        config={"event_id": "pyohio-2026"},
    )


@pytest.fixture
def video_connection(organization, fake_providers) -> ProviderConnection:
    connection = ProviderConnection(
        organization=organization,
        slug="fake-videos-2026",
        name="Fake video host (2026)",
        capability=Capability.VIDEO_HOST,
        provider="fake",
    )
    connection.set_credentials({"refresh_token": "test-refresh-token"})
    connection.save()
    return connection


@pytest.fixture
def video_binding(event, video_connection) -> EventProviderBinding:
    return EventProviderBinding.objects.create(
        event=event,
        capability=Capability.VIDEO_HOST,
        connection=video_connection,
        config={"playlist_id": "PLtest123"},
    )


@pytest.fixture
def fake_videos(fake_providers):
    """Set what `FakeVideoHost` reports, restoring the class attributes afterwards.

    Separate from `fake_talks` because the video host's list is *mutated* by `set_privacy`, so a test
    that installed videos and did not restore would hand the next one a playlist it did not ask for.
    """
    attributes = (
        "videos",
        "captions",
        "uploaded_captions",
        "privacy_changes",
        "set_privacy_error",
        "upload_captions_error",
        "ignore_privacy_writes",
    )
    original = {name: getattr(FakeVideoHost, name) for name in attributes}

    def install(*, videos=(), captions=None, set_privacy_error=None, upload_captions_error=None, ignore_writes=False):
        FakeVideoHost.videos = list(videos)
        FakeVideoHost.captions = dict(captions or {})
        FakeVideoHost.uploaded_captions = []
        FakeVideoHost.privacy_changes = []
        FakeVideoHost.set_privacy_error = set_privacy_error
        FakeVideoHost.upload_captions_error = upload_captions_error
        FakeVideoHost.ignore_privacy_writes = ignore_writes

    try:
        yield install
    finally:
        for name, value in original.items():
            setattr(FakeVideoHost, name, value)


@pytest.fixture
def fake_talks(fake_providers):
    """Set the records `FakeTalkSource` returns, restoring the class attributes afterwards.

    The fake holds them as class attributes, so a test that mutated them without restoring would
    leak into the next one.
    """
    original = (FakeTalkSource.speakers, FakeTalkSource.talks)

    def install(*, speakers=(), talks=()):
        FakeTalkSource.speakers = list(speakers)
        FakeTalkSource.talks = list(talks)

    try:
        yield install
    finally:
        FakeTalkSource.speakers, FakeTalkSource.talks = original
