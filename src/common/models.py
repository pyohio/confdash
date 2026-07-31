"""Abstract base model shared by every concrete model in the project."""

import uuid

from django.db import models


def uuid7() -> uuid.UUID:
    """Time-ordered UUID, stdlib as of Python 3.14.

    Wrapped rather than referenced directly so migrations record a stable, importable path and
    so there is one place to change if the implementation ever needs to move.
    """
    return uuid.uuid7()


class BaseModel(models.Model):
    """UUIDv7 primary key plus creation and update timestamps.

    UUIDv7 over UUIDv4 because it embeds a timestamp and therefore sorts by creation order:
    index locality on insert-heavy tables, and a usable default ordering without a separate
    column. Over an auto-incrementing integer because IDs appear in URLs that get emailed to
    speakers, and sequential integers there invite guessing at other people's records.

    Primary keys are never external identifiers. Provider-side IDs live in their own fields.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
