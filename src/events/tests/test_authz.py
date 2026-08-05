"""Organizer authorization.

Two invariants get explicit coverage because neither can be a database constraint:
cross-organization access is impossible, and a speaker magic link never confers organizer access.
"""

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from accounts.auth_method import AuthMethod, set_auth_method
from events.authz import get_membership, has_org_scope, organizations_for, require_org_scope
from events.models import OrganizationMembership
from events.scopes import Scope

pytestmark = pytest.mark.integration


def make_request(user, *, method: AuthMethod = AuthMethod.FEDERATED):
    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.user = user
    set_auth_method(request, method)
    return request


@pytest.fixture
def owner(organization, db):
    from accounts.models import User

    user = User.objects.create_user(email="owner@example.org")
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    return user


class TestGetMembership:
    def test_returns_the_membership_for_that_organization(self, organizer, organization):
        assert get_membership(organizer, organization) is not None

    def test_returns_none_for_a_different_organization(self, organizer, other_organization):
        """A membership in one org must never satisfy a check against another."""
        assert get_membership(organizer, other_organization) is None

    def test_returns_none_for_an_anonymous_user(self, organization):
        from django.contrib.auth.models import AnonymousUser

        assert get_membership(AnonymousUser(), organization) is None


class TestHasOrgScope:
    def test_an_unrestricted_membership_grants_every_scope(self, organizer, organization):
        """Today's all-or-nothing behavior."""
        request = make_request(organizer)
        for scope in Scope:
            assert has_org_scope(request, organization, scope) is True

    def test_a_restricted_membership_grants_only_its_scopes(self, organizer, organization):
        membership = get_membership(organizer, organization)
        membership.scopes = [Scope.PROGRAM, Scope.VIDEOS]
        membership.save()

        request = make_request(organizer)
        assert has_org_scope(request, organization, Scope.VIDEOS) is True
        assert has_org_scope(request, organization, Scope.SPONSORSHIP) is False

    def test_an_owner_holds_every_scope_even_when_restricted(self, owner, organization):
        """An owner restricted out of an area could not restore their own access."""
        membership = get_membership(owner, organization)
        membership.scopes = [Scope.COMMS]
        membership.save()

        assert has_org_scope(make_request(owner), organization, Scope.SPONSORSHIP) is True

    def test_cross_organization_access_is_impossible(self, organizer, other_organization):
        """A stated security invariant, and not expressible as a database constraint."""
        request = make_request(organizer)
        for scope in Scope:
            assert has_org_scope(request, other_organization, scope) is False

    def test_a_magic_link_session_never_grants_organizer_access(self, organizer, organization):
        """Even holding a real membership. Organizer access requires the organization's IdP."""
        request = make_request(organizer, method=AuthMethod.MAGIC_LINK)
        for scope in Scope:
            assert has_org_scope(request, organization, scope) is False

    def test_a_user_with_no_membership_is_refused(self, user, organization):
        assert has_org_scope(make_request(user), organization, Scope.PROGRAM) is False

    def test_accepts_a_plain_string_scope(self, organizer, organization):
        assert has_org_scope(make_request(organizer), organization, "videos") is True


class TestRequireOrgScope:
    def test_passes_silently_when_permitted(self, organizer, organization):
        require_org_scope(make_request(organizer), organization, Scope.PROGRAM)

    def test_raises_permission_denied_when_not(self, organizer, other_organization):
        with pytest.raises(PermissionDenied):
            require_org_scope(make_request(organizer), other_organization, Scope.PROGRAM)


class TestOrganizationsFor:
    def test_lists_only_organizations_the_user_belongs_to(self, organizer, organization, other_organization):
        assert organizations_for(make_request(organizer)) == [organization]

    def test_is_empty_for_a_magic_link_session(self, organizer):
        """A speaker session sees no organizer surface at all, memberships notwithstanding."""
        assert organizations_for(make_request(organizer, method=AuthMethod.MAGIC_LINK)) == []
