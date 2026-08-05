"""View decorators for organizer URLs.

Organizer URLs are path-scoped: `/o/<organization_slug>/<event_slug>/...`. The tenant is therefore a
routing concern, and this decorator is what turns it into an authorization decision before a view body
runs. It resolves the two slugs, checks the scope through `events.authz`, and hands the view real
objects instead of strings.

Why the tenant is in the path rather than derived from the object being acted on: deriving it means
authorization can only happen after a fetch, so every view has to remember to check, and a forgotten
check is indistinguishable from a working view until someone finds it. Here a wrong-organization URL
fails before any query the view would make.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest

from events.authz import require_org_scope
from events.models import Event
from events.scopes import Scope


def organizer_view(scope: Scope):
    """Require an organizer session with `scope` in the organization named in the URL.

    Wraps a view taking `(request, event, ...)`. The `organization_slug` and `event_slug` kwargs are
    consumed here, so the view signature stays about what it does rather than how it was addressed:

        @organizer_view(Scope.VIDEOS)
        def confirm_queue(request, event):
            ...

    A slug pair naming no event is `Http404`; one naming an event the caller may not reach is
    `PermissionDenied`. That does mean an outsider can tell a real event slug from a made-up one, which
    is acceptable: conference programmes are public, and the organization slug appears in the URL they
    were given. What must not leak is *who belongs to an organization and with what scope*, and
    `require_org_scope` covers that by refusing to say which of its three conditions failed.
    """

    def decorate(view):
        @wraps(view)
        def wrapper(request: HttpRequest, *args, organization_slug: str, event_slug: str, **kwargs):
            event = _resolve(organization_slug, event_slug)
            require_org_scope(request, event.organization, scope)
            return view(request, event, *args, **kwargs)

        return wrapper

    return decorate


def _resolve(organization_slug: str, event_slug: str) -> Event:
    """The event these two slugs name.

    Matched as a pair, never event-slug-alone: `2026` exists in every organization, and looking one up
    without its organization would serve another tenant's event to anyone who guessed the slug.
    """
    event = (
        Event.objects.select_related("organization")
        .filter(organization__slug=organization_slug, slug=event_slug)
        .first()
    )
    if event is None:
        raise Http404("No such event.")
    return event


def require_scope(request: HttpRequest, event: Event, scope: Scope) -> None:
    """Check a second scope inside a view already authorized for another one.

    For a page that shows one thing and offers an action needing more: the decorator covers the page,
    this covers the action.
    """
    require_org_scope(request, event.organization, scope)


def has_scope(request: HttpRequest, event: Event, scope: Scope) -> bool:
    """Whether to *show* something, as opposed to whether to allow it.

    Templates need this so a button an organizer cannot use is not rendered at all. It is never the
    check that protects the action: that one lives on the view handling it.
    """
    from events.authz import has_org_scope

    return has_org_scope(request, event.organization, scope)


__all__ = ["PermissionDenied", "has_scope", "organizer_view", "require_scope"]
