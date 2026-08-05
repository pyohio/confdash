"""Delete expired magic-link tokens.

Housekeeping, not security: an expired token is already refused on sight. Worth a command because these
rows accumulate one per invitation per resend and nothing else would ever remove them.

Consumed-but-unexpired tokens are kept. They are the record that a link was used, and they stop being
interesting on their own a few days later.
"""

import typer
from django_typer.management import Typer

from accounts import tokens

app = Typer(help=__doc__)


@app.command()
def main() -> None:
    removed = tokens.purge_expired()
    typer.echo(f"Removed {removed} expired login token(s).")
