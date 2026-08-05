"""Shared argument resolution for management commands.

Here rather than in one command because every command that acts on an event needs the same lookup,
and the ambiguity rule is a tenancy decision that should have one implementation.
"""

from django.core.management.base import CommandError

from events.models import Event


def resolve_event(slug: str, organization_slug: str | None = None) -> Event:
    """Find one event by slug.

    Event slugs are unique per organization, not globally, so '2026' can be ambiguous on a multi-tenant
    instance. Fail with the choices rather than picking one: guessing here would act on another
    organization's event.
    """
    events = Event.objects.filter(slug=slug).select_related("organization")
    if organization_slug:
        events = events.filter(organization__slug=organization_slug)

    matches = list(events)
    if not matches:
        raise CommandError(
            f"No event with slug {slug!r}." + (f" in organization {organization_slug!r}." if organization_slug else "")
        )
    if len(matches) > 1:
        options = ", ".join(f"--organization {e.organization.slug}" for e in matches)
        raise CommandError(f"Several organizations have an event named {slug!r}. Disambiguate with one of: {options}")

    return matches[0]
