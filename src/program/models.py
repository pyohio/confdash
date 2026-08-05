"""The program: talks and the people giving them.

A local mirror of whatever the event's `talk_source` provider holds, populated by `sync_program`.
Provider identifiers live in `external_id`, never as primary keys, so a provider swap or a re-issued
credential cannot orphan review state.

These rows are reachable by speakers reviewing their own videos, so they carry only the fields the
app uses. CFP review data (scores, reviewer comments, custom-question answers) is deliberately absent
and belongs to a future program-committee surface with its own models. See plans/decisions.md.
"""

from django.db import models

from common.models import BaseModel


class Speaker(BaseModel):
    """A person giving a talk at one event.

    Event-scoped, so the same human speaking in 2026 and 2027 is two rows. Provider speaker codes
    are event-scoped, and names and bios change between years. `user` is the cross-year identity
    when one is needed, resolved by email at first login.
    """

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="speakers")
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="speaker_profiles",
        help_text="Set when this speaker first logs in. Null until then.",
    )

    external_id = models.CharField(max_length=128, help_text="Provider's speaker code, e.g. Pretalx 'TXY7EW'.")

    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, help_text="Where review invitations go.")
    biography = models.TextField(blank=True)
    avatar_url = models.URLField(blank=True, max_length=500)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "external_id"], name="unique_speaker_external_id_per_event"),
        ]
        ordering = ["name"]
        indexes = [models.Index(fields=["event", "email"])]

    def __str__(self) -> str:
        return self.name or self.external_id


class Talk(BaseModel):
    """One accepted session."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="talks")

    external_id = models.CharField(max_length=128, help_text="Provider's submission code.")

    title = models.CharField(max_length=500)
    abstract = models.TextField(blank=True)
    description = models.TextField(blank=True)

    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    session_type = models.CharField(max_length=100, blank=True, help_text="e.g. 'Talk', 'Keynote'.")
    state = models.CharField(max_length=50, blank=True, help_text="Provider's state, e.g. 'confirmed'.")

    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)

    # A hard publication guard, not a preference: a talk marked this way must never be released,
    # whatever a video's review state says.
    do_not_record = models.BooleanField(
        default=False,
        help_text="Speaker or organizer opted out of recording. Blocks publication entirely.",
    )

    speakers = models.ManyToManyField(Speaker, through="TalkSpeaker", related_name="talks")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "external_id"], name="unique_talk_external_id_per_event"),
        ]
        ordering = ["title"]
        indexes = [models.Index(fields=["event", "state"])]

    def __str__(self) -> str:
        return self.title


class TalkSpeaker(BaseModel):
    """Join row.

    Explicit rather than a bare ManyToMany table so the sync can add and remove links idempotently,
    and so a link carries the timestamps `BaseModel` provides.
    """

    talk = models.ForeignKey(Talk, on_delete=models.CASCADE, related_name="talk_speakers")
    speaker = models.ForeignKey(Speaker, on_delete=models.CASCADE, related_name="talk_speakers")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["talk", "speaker"], name="unique_talk_speaker"),
        ]
        ordering = ["talk", "speaker"]

    def __str__(self) -> str:
        return f"{self.speaker} on {self.talk}"
