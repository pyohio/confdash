"""Shared pytest fixtures.

Plain factory functions rather than a factory library: fixtures call the same service-layer and
model code the application does, so a test that passes proves the real path works.
"""

import pytest

from accounts.models import User
from events.models import Event, Organization, OrganizationMembership


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
