"""Execute pending provider writes.

Run it by hand after approving videos, or from cron if something is waiting on tomorrow's quota. Safe to
run concurrently with itself: rows are claimed with `SKIP LOCKED`, so two drains take disjoint work.
"""

from typing import Annotated

import typer
from django_typer.management import Typer

from events.cli import resolve_event
from integrations.models import ProviderWrite
from integrations.outbox import drain

app = Typer(help=__doc__)


@app.command()
def main(
    event: Annotated[
        str | None,
        typer.Option("--event", help="Event slug. Omit to drain every event."),
    ] = None,
    organization: Annotated[
        str | None,
        typer.Option("--organization", help="Organization slug. Required only if the event slug is ambiguous."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Most writes to attempt in this run."),
    ] = 25,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what is due without writing anything."),
    ] = False,
) -> None:
    target = resolve_event(event, organization) if event else None

    if dry_run:
        _report_due(target)
        return

    result = drain(event=target, limit=limit)

    scope = str(target) if target else "all events"
    typer.echo(f"Drained provider writes for {scope}.")
    typer.echo(f"  confirmed: {result.confirmed}")
    typer.echo(f"  failed:    {result.failed}")
    typer.echo(f"  deferred:  {result.deferred}")

    if result.requeued:
        typer.echo(f"  requeued:  {result.requeued} abandoned in flight by an earlier run")
    if not result.attempted:
        typer.echo("  nothing was due.")


def _report_due(target) -> None:
    """List what a real run would attempt.

    Worth having because these writes cost provider allowance: seeing the queue before spending it is
    cheaper than reading the log afterwards.
    """
    due = ProviderWrite.objects.filter(state=ProviderWrite.State.PENDING).select_related("event")
    if target is not None:
        due = due.filter(event=target)

    rows = list(due.order_by("created_at"))
    if not rows:
        typer.echo("Nothing pending.")
        return

    typer.echo(f"{len(rows)} pending write(s):")
    for write in rows:
        waiting = f" not before {write.not_before.isoformat()}" if write.not_before else ""
        attempts = f" attempts={write.attempts}" if write.attempts else ""
        typer.echo(f"  {write.event} {write.operation} {write.target_external_id}{attempts}{waiting}")
