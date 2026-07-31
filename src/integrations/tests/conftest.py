"""Fixtures for integration-layer tests."""

import pytest

from integrations import registry
from integrations.models import EventProviderBinding, ProviderConnection
from integrations.providers.base import Capability
from integrations.tests.fakes import FakeTalkSource, FakeVideoHost


@pytest.fixture
def fake_providers():
    """Register the fake adapters for the duration of one test.

    The registry is module-level state, so it is snapshotted and restored rather than cleared:
    real adapters registered at app startup must still be there afterwards.
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
