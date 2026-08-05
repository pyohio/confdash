"""Pull talks and speakers from the event's configured talk source."""

from typing import Annotated

import typer
from django.core.management.base import CommandError
from django_typer.management import Typer

from events.models import Event
from program.services import sync_program

app = Typer(help=__doc__)


@app.command()
def main(
    event: Annotated[str, typer.Option("--event", help="Event slug, e.g. '2026'.")],
    organization: Annotated[
        str | None,
        typer.Option("--organization", help="Organization slug. Required only if the event slug is ambiguous."),
    ] = None,
) -> None:
    target = _resolve_event(event, organization)

    result = sync_program(target)

    typer.echo(f"Synced {target} from its talk source.")
    typer.echo(f"  speakers: {result.speakers_created} created, {result.speakers_updated} updated")
    typer.echo(f"  talks:    {result.talks_created} created, {result.talks_updated} updated")
    typer.echo(f"  links:    {result.links_created} created, {result.links_removed} removed")

    if result.talks_absent or result.speakers_absent:
        # Surfaced rather than silent: these rows were kept, and a large number here usually means
        # the provider or its credential changed rather than that the program shrank.
        typer.echo(
            f"  kept but not reported by the provider: {result.talks_absent} talks, {result.speakers_absent} speakers"
        )
        if result.absent_talk_ids:
            typer.echo(f"    talk ids: {', '.join(result.absent_talk_ids)}")


def _resolve_event(slug: str, organization_slug: str | None) -> Event:
    """Find one event by slug.

    Event slugs are unique per organization, not globally, so '2026' can be ambiguous on a
    multi-tenant instance. Fail with the choices rather than picking one.
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
