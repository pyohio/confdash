"""Organizer authorization: the single place that decides organization-scoped access.

Three independent conditions, all required:

1. The session authenticated by a mechanism allowed to reach organizer views (`accounts.auth_method`).
2. The user holds an `OrganizationMembership` in *that* organization.
3. The membership grants the scope being asked for.

Kept as one predicate so there is one thing to audit and one thing to test. `CLAUDE.md` states that
cross-organization access must be impossible, and it cannot be a database constraint, so it has to be
a function every organizer path goes through.

The view decorator lands with the first organizer view, since it needs the URL shape that is still
open. These predicates are the load-bearing part and are URL-independent.
"""

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from accounts.auth_method import permits_organizer_access
from events.models import Organization, OrganizationMembership
from events.scopes import Scope


def get_membership(user, organization: Organization) -> OrganizationMembership | None:
    """The user's membership in this organization, or None.

    Always filtered by organization rather than fetched by user and compared afterwards, so a
    caller cannot accidentally act on a membership in a different organization.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    return OrganizationMembership.objects.filter(user=user, organization=organization).first()


def has_org_scope(request: HttpRequest, organization: Organization, scope: Scope | str) -> bool:
    """Whether this request may act on `organization` within `scope`."""
    if not permits_organizer_access(request):
        return False
    membership = get_membership(request.user, organization)
    if membership is None:
        return False
    return membership.has_scope(scope)


def require_org_scope(request: HttpRequest, organization: Organization, scope: Scope | str) -> None:
    """Raise `PermissionDenied` unless this request may act on `organization` within `scope`.

    Deliberately does not distinguish "not a member" from "wrong scope" from "wrong login method":
    the response must not tell an outsider whether an organization exists or who belongs to it.
    """
    if not has_org_scope(request, organization, scope):
        raise PermissionDenied


def organizations_for(request: HttpRequest) -> list[Organization]:
    """Organizations this request may act in as an organizer, for building a chooser.

    Empty for a magic-link session even when the user holds memberships, which is the point: a
    speaker session sees no organizer surface at all.
    """
    if not permits_organizer_access(request):
        return []
    return list(Organization.objects.filter(memberships__user=request.user).order_by("name"))
