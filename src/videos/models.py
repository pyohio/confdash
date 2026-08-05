"""Videos and their review state.

A `Video` mirrors one recording on the event's `video_host`. The videography team uploads to the
organization's channel and leaves everything unlisted; this app reads that playlist, matches videos to
talks, collects speaker approval, and only then publishes.

Two independent axes of state, because they really are independent:

- `review_state`: what the reviewer has said.
- `publication_state`: what the provider has confirmed.

Who reviews depends on the matching outcome. A video matched to a talk goes to that talk's speakers. A
standalone video, a welcome or closing remarks or a keynote recording, has no speaker to ask and is
reviewed and approved by staff instead. Either way it reaches the same `approved` state and the same
publication path, so nothing standalone is stranded unpublishable.

`publication_state` and `privacy_status` describe **confirmed provider state, never intent**. Nothing
here is set on the strength of having asked a provider to do something; see plans/provider-writes.md.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.models import BaseModel


class Video(BaseModel):
    """One recording, matched to at most one talk."""

    class ReviewState(models.TextChoices):
        PENDING = "pending", "Pending"
        INVITED = "invited", "Invited"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        APPROVED = "approved", "Approved"

    class PublicationState(models.TextChoices):
        UNPUBLISHED = "unpublished", "Unpublished"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHED = "published", "Published"

    class PrivacyStatus(models.TextChoices):
        PRIVATE = "private", "Private"
        UNLISTED = "unlisted", "Unlisted"
        PUBLIC = "public", "Public"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="videos")
    talk = models.ForeignKey(
        "program.Talk",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="videos",
        help_text="Null until matched. A talk may have more than one video.",
    )

    external_id = models.CharField(max_length=128, help_text="Provider's video id, e.g. a YouTube video id.")

    # As uploaded. Kept verbatim so a metadata-normalization dry run has something to compare against
    # and something to revert to.
    title = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)

    privacy_status = models.CharField(
        max_length=20,
        choices=PrivacyStatus.choices,
        default=PrivacyStatus.PRIVATE,
        help_text="As last reported by the provider.",
    )
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, help_text="Provider's upload timestamp.")

    # --- Matching -----------------------------------------------------------
    #
    # `talk` and `standalone` together give three states one nullable FK cannot express. Without the
    # flag an organizer cannot tell "nobody has looked at this" from "there is correctly nothing to
    # link", and the queue would keep re-presenting settled videos.
    standalone = models.BooleanField(
        default=False,
        help_text=(
            "Deliberately has no talk: a welcome, closing remarks, a keynote recording. "
            "Reviewed and approved by staff rather than by a speaker."
        ),
    )
    matched_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matched_videos",
    )
    matched_at = models.DateTimeField(null=True, blank=True)

    # --- Review -------------------------------------------------------------

    review_state = models.CharField(max_length=20, choices=ReviewState.choices, default=ReviewState.PENDING)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_videos",
    )

    publication_state = models.CharField(
        max_length=20,
        choices=PublicationState.choices,
        default=PublicationState.UNPUBLISHED,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "external_id"], name="unique_video_external_id_per_event"),
            models.CheckConstraint(
                condition=models.Q(standalone=False) | models.Q(talk__isnull=True),
                name="standalone_video_has_no_talk",
            ),
        ]
        ordering = ["title", "external_id"]
        indexes = [
            models.Index(fields=["event", "review_state"]),
            models.Index(fields=["event", "publication_state"]),
        ]

    def __str__(self) -> str:
        return self.title or self.external_id

    @property
    def is_matched(self) -> bool:
        return self.talk_id is not None

    @property
    def needs_matching(self) -> bool:
        """Awaiting an organizer decision, as opposed to settled either way."""
        return self.talk_id is None and not self.standalone

    @property
    def review_track(self) -> str | None:
        """Who reviews this video, or None while matching is undecided.

        Derived rather than stored, because it follows entirely from the matching outcome: a video with
        a talk has speakers to ask, and a standalone one does not. Storing it would let the two drift.
        """
        if self.talk_id is not None:
            return "speaker"
        return "staff" if self.standalone else None

    @property
    def may_be_published(self) -> bool:
        """Whether publishing this video is permitted right now.

        Checked when a write executes rather than only when it is queued: approval can be withdrawn and
        a talk can be marked do-not-record in between. See plans/provider-writes.md.

        Approval alone is never enough. A video still awaiting a matching decision cannot publish even
        if approved, because there is no answer to who approved it or on whose behalf.
        """
        if self.review_state != self.ReviewState.APPROVED:
            return False
        if self.talk is not None:
            return not self.talk.do_not_record
        return self.standalone

    def clean(self):
        """Validate what the database cannot express as a constraint."""
        super().clean()
        errors = {}

        if self.talk_id and self.event_id and self.talk.event_id != self.event_id:
            # Cross-event matching would attach one event's video to another's talk. Not expressible
            # as a DB constraint without denormalizing event onto the join.
            errors["talk"] = "Talk belongs to a different event than this video."

        if self.standalone and self.talk_id:
            errors["standalone"] = "A video marked standalone cannot also be linked to a talk."

        if errors:
            raise ValidationError(errors)
