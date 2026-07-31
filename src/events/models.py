"""Tenancy: organizations and the events they run.

Two levels. An `Organization` is a tenant; an `Event` is one iteration of a conference (PyOhio
2026). Every model in the project that holds conference data hangs off `Event`.

Multi-year grouping is `Event.series`, a slug rather than a table. See plans/decisions.md for
why the three-level Organization/Series/Edition shape was rejected.

Scoping is enforced in application code, not row-level security.
"""

from email.utils import formataddr

from django.db import models
from django.utils.text import slugify

from common.models import BaseModel


class Organization(BaseModel):
    """A tenant: the group that runs one or more conferences."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)

    is_active = models.BooleanField(default=True)

    # Sender identity for mail this organization's events send, chiefly speaker magic links and
    # review invitations. Real columns rather than `settings` keys because they need email
    # validation and are read on every send.
    #
    # Blank falls back to the deployment's DEFAULT_FROM_EMAIL. Setting a custom address only
    # works if that domain is verified with the configured email provider, which is an operator
    # step outside this app.
    from_email = models.EmailField(
        blank=True,
        help_text="Sender address for this organization's mail. Blank uses the deployment default. "
        "The domain must be verified with the email provider.",
    )
    from_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Display name shown alongside the sender address, e.g. 'PyOhio'. Defaults to the organization name.",
    )

    # Policy that does not yet deserve a column. Events override these; see resolve_setting.
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def sender_address(self) -> str:
        """Return the From header for mail sent on this organization's behalf.

        Falls back to the deployment default when the organization has not set its own, so a
        single-tenant install needs no per-organization configuration at all.
        """
        from django.conf import settings as django_settings

        if not self.from_email:
            return django_settings.DEFAULT_FROM_EMAIL

        display_name = self.from_name or self.name
        return formataddr((display_name, self.from_email))


class OrganizationMembership(BaseModel):
    """A user's role within an organization.

    Organizer authorization is org-scoped from the start, rather than global `is_staff`, so that
    hosting a second organization does not mean its organizers can see the first one's data.
    `is_staff` remains strictly about Django admin access.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ORGANIZER = "organizer", "Organizer"
        VIEWER = "viewer", "Viewer"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ORGANIZER)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="unique_membership_per_org"),
        ]
        ordering = ["organization", "user"]

    def __str__(self) -> str:
        return f"{self.user} in {self.organization} ({self.role})"

    @property
    def can_manage(self) -> bool:
        return self.role in {self.Role.OWNER, self.Role.ORGANIZER}


class Event(BaseModel):
    """One iteration of a conference.

    `slug` is unique within the organization, so two organizations can both have a "2026".
    `series` groups iterations of the same conference for year-over-year reporting.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="events")

    slug = models.SlugField(max_length=100, help_text="Unique within the organization, e.g. '2026'.")
    name = models.CharField(max_length=200, help_text="Display name, e.g. 'PyOhio 2026'.")
    series = models.SlugField(
        max_length=100,
        blank=True,
        help_text="Groups iterations of the same conference across years, e.g. 'pyohio'.",
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # Event-local time, for deadlines and schedule display. Stored per event because an
    # organization may run events in different timezones.
    timezone = models.CharField(max_length=64, default="UTC")

    is_active = models.BooleanField(default=True)

    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="unique_event_slug_per_org"),
        ]
        ordering = ["-start_date", "-created_at"]
        indexes = [models.Index(fields=["organization", "series"])]

    def __str__(self) -> str:
        return self.name

    def resolve_setting(self, key: str, default=None):
        """Return an event setting, falling back to the organization's, then to `default`.

        Lets an organization set a policy once and an individual event override it.
        """
        if key in self.settings:
            return self.settings[key]
        return self.organization.settings.get(key, default)
