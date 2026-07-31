"""User identity.

One `User` model for both audiences. Organizers and speakers differ in authorization, not in
identity: an organizer is a user with an `OrganizationMembership`, a speaker is a user a
`Speaker` row points at. The same person can be both, which happens whenever an organizer also
gives a talk.

Authentication is passwordless (emailed magic links), so users have no usable password by
default. `createsuperuser` still sets a real one, because admin access needs a way in that does
not depend on outbound email working.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from common.models import uuid7


class UserManager(BaseUserManager):
    """Manager keyed on email rather than username."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Unusable rather than blank: a blank password field would still be a login target.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Superusers must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    email = models.EmailField(unique=True)

    # A single name field, not first/last. Speaker names arrive from provider APIs as one
    # string, and splitting them is lossy for a large share of real names.
    name = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False,
        help_text="Grants access to the Django admin. Organizer permissions come from OrganizationMembership instead.",
    )

    # Escape hatch for per-user preferences and flags, so adding one does not need a migration.
    # Anything that needs querying, validation, or a constraint graduates to a real column.
    data = models.JSONField(default=dict, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    def get_short_name(self) -> str:
        return self.name or self.email.split("@")[0]

    def get_full_name(self) -> str:
        return self.name or self.email

    @property
    def organizations(self):
        """Organizations this user has any membership in."""
        from events.models import Organization

        return Organization.objects.filter(memberships__user=self)


class LoginToken(models.Model):
    """Single-use magic-link token.

    Only a hash is stored, so a database leak does not hand over working login links. The raw
    token exists just long enough to be put in an email.

    Not a `BaseModel` subclass: the natural primary key here is the token hash itself, and a
    second surrogate UUID would add nothing.
    """

    token_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_tokens")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    # Where to send the user after a successful login, so an invitation link can deep-link to
    # the specific video being reviewed.
    next_url = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "expires_at"])]

    def __str__(self) -> str:
        return f"login token for {self.user_id}"

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()
