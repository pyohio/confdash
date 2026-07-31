"""Base model tests: UUIDv7 primary keys and timestamps."""

import time
import uuid

import pytest

from common.models import uuid7
from events.models import Organization

# Markers are per-test here rather than module-level: the database-backed tests below must not
# also be collected by `just test-unit`, which runs with no database available.


@pytest.mark.unit
def test_uuid7_is_version_7():
    assert uuid7().version == 7


@pytest.mark.unit
def test_uuid7_values_are_unique():
    assert len({uuid7() for _ in range(1000)}) == 1000


@pytest.mark.unit
def test_uuid7_sorts_by_creation_time():
    """The reason for v7 over v4: time ordering gives index locality and a usable default sort."""
    first = uuid7()
    time.sleep(0.005)
    second = uuid7()
    assert first < second
    assert str(first) < str(second)


@pytest.mark.integration
def test_pk_is_a_uuid_assigned_before_save(db):
    """Assigned client-side, so related objects can be built before any INSERT."""
    org = Organization(name="PyOhio", slug="pyohio")
    assert isinstance(org.pk, uuid.UUID)
    assert org.pk.version == 7


@pytest.mark.integration
def test_timestamps_are_managed(db):
    org = Organization.objects.create(name="PyOhio", slug="pyohio")
    created, updated = org.created_at, org.updated_at
    assert created is not None

    org.name = "PyOhio Renamed"
    org.save()
    org.refresh_from_db()

    assert org.created_at == created
    assert org.updated_at > updated
