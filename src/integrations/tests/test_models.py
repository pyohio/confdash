"""ProviderConnection and EventProviderBinding model tests."""

import pytest
from django.db import IntegrityError

from integrations.models import EventProviderBinding, ProviderConnection, SyncRun
from integrations.providers.base import Capability

pytestmark = pytest.mark.integration


def test_credentials_are_not_stored_as_plaintext(talk_connection):
    """Guards the property that matters in a database dump."""
    raw = ProviderConnection.objects.filter(pk=talk_connection.pk).values_list("credentials_encrypted", flat=True)[0]
    assert "test-token" not in raw
    assert talk_connection.get_credentials() == {"api_token": "test-token"}


def test_several_connections_for_one_provider_are_allowed(organization, fake_providers):
    """Pretalx issues credentials per event, so an org holds one connection per year."""
    for year in ("2026", "2027"):
        connection = ProviderConnection(
            organization=organization,
            slug=f"fake-talks-{year}",
            name=f"Fake talk source ({year})",
            capability=Capability.TALK_SOURCE,
            provider="fake",
        )
        connection.set_credentials({"api_token": f"token-{year}"})
        connection.save()

    assert organization.provider_connections.filter(provider="fake").count() == 2


def test_connection_slug_is_unique_within_an_organization(organization, talk_connection, fake_providers):
    duplicate = ProviderConnection(
        organization=organization,
        slug=talk_connection.slug,
        name="Duplicate",
        capability=Capability.TALK_SOURCE,
        provider="fake",
    )
    with pytest.raises(IntegrityError):
        duplicate.save()


def test_same_slug_in_a_different_organization_is_allowed(other_organization, talk_connection, fake_providers):
    twin = ProviderConnection(
        organization=other_organization,
        slug=talk_connection.slug,
        name="Same slug, different tenant",
        capability=Capability.TALK_SOURCE,
        provider="fake",
    )
    twin.save()
    assert twin.pk


def test_one_binding_per_capability_per_event(event, talk_binding, talk_connection):
    duplicate = EventProviderBinding(
        event=event,
        capability=Capability.TALK_SOURCE,
        connection=talk_connection,
        config={"event_id": "other"},
    )
    with pytest.raises(IntegrityError):
        duplicate.save()


def test_connection_in_use_cannot_be_deleted(talk_binding, talk_connection):
    """PROTECT, so removing a connection cannot silently break a configured event."""
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        talk_connection.delete()


def test_missing_credential_keys_reports_required_only(talk_connection):
    assert talk_connection.missing_credential_keys() == []
    talk_connection.set_credentials({})
    assert talk_connection.missing_credential_keys() == ["api_token"]


def test_mark_verified_clears_a_previous_error(talk_connection):
    talk_connection.mark_verified(error="boom")
    talk_connection.refresh_from_db()
    assert talk_connection.last_verify_error == "boom"

    talk_connection.mark_verified()
    talk_connection.refresh_from_db()
    assert talk_connection.last_verify_error == ""
    assert talk_connection.last_verified_at is not None


def test_sync_run_duration_is_none_until_finished(event):
    run = SyncRun.objects.create(event=event, capability=Capability.TALK_SOURCE, provider="fake")
    assert run.duration_seconds is None
