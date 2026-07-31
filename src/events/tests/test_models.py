"""Tenancy model tests.

The multi-org, multi-year requirement is the reason this project exists rather than continuing
the legacy one, so the scoping rules get explicit coverage.
"""

import pytest
from django.db import IntegrityError

from events.models import Event, Organization, OrganizationMembership

pytestmark = pytest.mark.integration


class TestOrganization:
    def test_slug_is_derived_from_name_when_omitted(self, db):
        org = Organization.objects.create(name="Django Girls Cleveland")
        assert org.slug == "django-girls-cleveland"

    def test_explicit_slug_is_kept(self, db):
        org = Organization.objects.create(name="PyOhio", slug="pyohio")
        assert org.slug == "pyohio"

    def test_slug_is_globally_unique(self, organization):
        with pytest.raises(IntegrityError):
            Organization.objects.create(name="Another", slug=organization.slug)


class TestEvent:
    def test_slug_is_unique_within_an_organization(self, organization, event):
        with pytest.raises(IntegrityError):
            Event.objects.create(organization=organization, slug=event.slug, name="Duplicate")

    def test_two_organizations_can_both_have_a_2026(self, organization, other_organization, event):
        """The core multi-tenancy property: event slugs are namespaced by organization."""
        twin = Event.objects.create(organization=other_organization, slug="2026", name="OtherConf 2026")

        assert twin.slug == event.slug
        assert twin.organization != event.organization

    def test_series_groups_iterations(self, organization, event):
        Event.objects.create(organization=organization, slug="2027", name="PyOhio 2027", series="pyohio")
        Event.objects.create(organization=organization, slug="mini", name="PyOhio Mini", series="pyohio-mini")

        in_series = Event.objects.filter(organization=organization, series="pyohio")

        assert in_series.count() == 2
        assert {e.slug for e in in_series} == {"2026", "2027"}


class TestSettingResolution:
    """Events override organization defaults, so an org sets policy once."""

    def test_falls_back_to_the_organization(self, organization, event):
        organization.settings = {"release_policy": "hold"}
        organization.save()

        assert event.resolve_setting("release_policy") == "hold"

    def test_event_value_wins(self, organization, event):
        organization.settings = {"release_policy": "hold"}
        organization.save()
        event.settings = {"release_policy": "immediate"}

        assert event.resolve_setting("release_policy") == "immediate"

    def test_default_is_used_when_neither_sets_it(self, event):
        assert event.resolve_setting("release_policy", "immediate") == "immediate"

    def test_a_falsy_event_value_still_wins(self, organization, event):
        """Presence, not truthiness: an event explicitly setting False must override True."""
        organization.settings = {"captions_required": True}
        organization.save()
        event.settings = {"captions_required": False}

        assert event.resolve_setting("captions_required") is False


class TestMembership:
    def test_a_user_has_one_membership_per_organization(self, organization, user):
        OrganizationMembership.objects.create(organization=organization, user=user)
        with pytest.raises(IntegrityError):
            OrganizationMembership.objects.create(organization=organization, user=user)

    def test_a_user_can_belong_to_several_organizations(self, organization, other_organization, user):
        OrganizationMembership.objects.create(organization=organization, user=user)
        OrganizationMembership.objects.create(organization=other_organization, user=user)

        assert user.organizations.count() == 2

    def test_can_manage_excludes_viewers(self, organization, user):
        membership = OrganizationMembership.objects.create(
            organization=organization, user=user, role=OrganizationMembership.Role.VIEWER
        )
        assert membership.can_manage is False

        membership.role = OrganizationMembership.Role.OWNER
        assert membership.can_manage is True

    def test_organizations_property_excludes_unrelated_orgs(self, organization, other_organization, organizer):
        assert list(organizer.organizations) == [organization]
