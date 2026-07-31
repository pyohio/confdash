"""Resolver and binding-validation tests.

Misconfiguration is the expected case here: an organizer setting up an event will get something
wrong, and the error they see is part of the interface. Each failure mode is covered.

The cross-organization test is the important one. Credentials must never be usable by another
tenant's event, and that rule cannot be a database constraint.
"""

import pytest
from django.core.exceptions import ValidationError

from integrations.models import EventProviderBinding, ProviderConnection
from integrations.providers.base import Capability
from integrations.resolver import (
    IntegrationNotConfigured,
    get_binding,
    resolve_adapter,
    verify_connection,
)
from integrations.tests.fakes import FakeTalkSource

pytestmark = pytest.mark.integration


def test_resolves_a_configured_adapter(event, talk_binding):
    adapter = resolve_adapter(event, Capability.TALK_SOURCE)
    assert isinstance(adapter, FakeTalkSource)
    assert adapter.credentials == {"api_token": "test-token"}


def test_event_config_overrides_connection_config(event, talk_connection):
    """An organization sets defaults; an event overrides only what differs."""
    talk_connection.config = {"api_base_url": "https://org.example.org", "shared": "from-connection"}
    talk_connection.save()
    EventProviderBinding.objects.create(
        event=event,
        capability=Capability.TALK_SOURCE,
        connection=talk_connection,
        config={"event_id": "pyohio-2026", "api_base_url": "https://event.example.org"},
    )

    adapter = resolve_adapter(event, Capability.TALK_SOURCE)

    assert adapter.config["api_base_url"] == "https://event.example.org"
    assert adapter.config["shared"] == "from-connection"


def test_missing_binding_is_actionable(event):
    with pytest.raises(IntegrationNotConfigured, match="no talk_source provider configured"):
        resolve_adapter(event, Capability.TALK_SOURCE)


def test_inactive_binding_is_refused(event, talk_binding):
    talk_binding.is_active = False
    talk_binding.save()
    with pytest.raises(IntegrationNotConfigured, match="inactive"):
        resolve_adapter(event, Capability.TALK_SOURCE)


def test_inactive_connection_is_refused(event, talk_binding, talk_connection):
    """Deactivating a connection must stop every event that uses it."""
    talk_connection.is_active = False
    talk_connection.save()
    with pytest.raises(IntegrationNotConfigured, match="inactive"):
        resolve_adapter(event, Capability.TALK_SOURCE)


def test_missing_credentials_are_reported_before_any_request(event, talk_binding, talk_connection):
    talk_connection.set_credentials({})
    talk_connection.save()
    with pytest.raises(IntegrationNotConfigured, match="missing required credentials: api_token"):
        resolve_adapter(event, Capability.TALK_SOURCE)


def test_missing_event_config_is_reported(event, talk_connection):
    EventProviderBinding.objects.create(
        event=event,
        capability=Capability.TALK_SOURCE,
        connection=talk_connection,
        config={},
    )
    with pytest.raises(IntegrationNotConfigured, match="missing required config: event_id"):
        resolve_adapter(event, Capability.TALK_SOURCE)


def test_get_binding_returns_the_binding(event, talk_binding):
    assert get_binding(event, "talk_source") == talk_binding


class TestBindingValidation:
    """`clean()` catches what the database cannot express."""

    def test_cross_organization_binding_is_rejected(self, event, other_organization, fake_providers):
        """A tenant's credentials must not be reachable from another tenant's event."""
        foreign = ProviderConnection(
            organization=other_organization,
            slug="fake-talks",
            name="Someone else's talk source",
            capability=Capability.TALK_SOURCE,
            provider="fake",
        )
        foreign.set_credentials({"api_token": "not-yours"})
        foreign.save()

        binding = EventProviderBinding(
            event=event,
            capability=Capability.TALK_SOURCE,
            connection=foreign,
            config={"event_id": "pyohio-2026"},
        )

        with pytest.raises(ValidationError, match="different organization"):
            binding.full_clean()

    def test_capability_mismatch_is_rejected(self, event, talk_connection):
        binding = EventProviderBinding(
            event=event,
            capability=Capability.VIDEO_HOST,
            connection=talk_connection,
            config={"playlist_id": "PL123"},
        )
        with pytest.raises(ValidationError, match="provides talk_source"):
            binding.full_clean()

    def test_missing_required_config_is_rejected(self, event, talk_connection):
        binding = EventProviderBinding(
            event=event,
            capability=Capability.TALK_SOURCE,
            connection=talk_connection,
            config={},
        )
        with pytest.raises(ValidationError, match="event_id"):
            binding.full_clean()

    def test_unregistered_provider_is_rejected_on_the_connection(self, organization):
        connection = ProviderConnection(
            organization=organization,
            slug="mystery",
            name="Mystery provider",
            capability=Capability.TALK_SOURCE,
            provider="does-not-exist",
        )
        with pytest.raises(ValidationError):
            connection.full_clean()


class TestVerifyConnection:
    def test_success_stamps_last_verified_at(self, talk_connection):
        ok, error = verify_connection(talk_connection)

        assert (ok, error) == (True, "")
        talk_connection.refresh_from_db()
        assert talk_connection.last_verified_at is not None
        assert talk_connection.last_verify_error == ""

    def test_provider_failure_is_recorded_not_raised(self, talk_connection, monkeypatch):
        """This backs an admin action, so a provider blowing up must not become a 500."""
        monkeypatch.setattr(FakeTalkSource, "check_error", RuntimeError("bad token"), raising=False)
        try:
            ok, error = verify_connection(talk_connection)
        finally:
            monkeypatch.setattr(FakeTalkSource, "check_error", None, raising=False)

        assert ok is False
        assert "bad token" in error
        talk_connection.refresh_from_db()
        assert talk_connection.last_verified_at is None
        assert "bad token" in talk_connection.last_verify_error

    def test_missing_credentials_fail_without_calling_the_provider(self, talk_connection):
        talk_connection.set_credentials({})
        talk_connection.save()

        ok, error = verify_connection(talk_connection)

        assert ok is False
        assert "api_token" in error
