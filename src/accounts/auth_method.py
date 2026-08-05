"""How the current session proved its identity.

Organizer access derives from membership the organization administers in its own identity provider,
so revoking there revokes here. `OrganizationMembership` rows cache the result of that check, which
makes them a second path to the same access: without this distinction, a speaker magic link would
satisfy organizer views and someone removed from the org's IdP would keep working.

So the session records which mechanism authenticated it, and organizer authorization requires an
allowed one. See `events.authz`.

The allow-list is deliberately a list rather than a "not magic link" test: a mechanism added later
should have to be granted organizer access explicitly, not inherit it by not being excluded.
"""

from enum import StrEnum

from django.http import HttpRequest

SESSION_KEY = "auth_method"


class AuthMethod(StrEnum):
    #: Authenticated against an organization's identity provider.
    FEDERATED = "federated"
    #: Emailed single-use link. Speaker-scope only.
    MAGIC_LINK = "magic_link"
    #: Password, which only deployment operators have. `createsuperuser` is the sole way to set one.
    PASSWORD = "password"  # noqa: S105 — names a mechanism, not a credential


def set_auth_method(request: HttpRequest, method: AuthMethod) -> None:
    """Record the mechanism that authenticated this session.

    Call after `django.contrib.auth.login()`. That cycles the session key but preserves session
    data, so either order works; after is clearer about what is being described.
    """
    request.session[SESSION_KEY] = str(method)


def get_auth_method(request: HttpRequest) -> AuthMethod | None:
    """The session's authentication mechanism, or None if it predates this being recorded."""
    raw = request.session.get(SESSION_KEY)
    if raw is None:
        return None
    try:
        return AuthMethod(raw)
    except ValueError:
        return None


def permits_organizer_access(request: HttpRequest) -> bool:
    """Whether this session's mechanism may reach organizer-scoped views at all.

    Independent of membership and scope, both of which `events.authz` checks separately. A session
    with no recorded mechanism is refused: failing closed matters more here than accommodating
    sessions created before this existed.
    """
    method = get_auth_method(request)
    if method == AuthMethod.FEDERATED:
        return True
    # Operators authenticate by password and are provisioned by hand, so a password session is not
    # a way around an organization's IdP. Nobody obtains a password by joining a GitHub org.
    if method == AuthMethod.PASSWORD:
        return bool(getattr(request.user, "is_superuser", False))
    return False
