"""Provider connections and the per-event bindings that select them.

Two levels, because credentials and event-specific settings have different lifetimes:

- `ProviderConnection` is organization-owned and holds credentials. Onboarding state.
- `EventProviderBinding` picks one connection per capability for an event and adds the
  event-specific config. Per-iteration state.

An organization may hold several connections for the same provider, because some providers issue
credentials per event: Pretalx does, so PyOhio has `pretalx-pyohio-2026`, `pretalx-pyohio-2027`,
and so on. Uniqueness is therefore `(organization, slug)`, never `(organization, provider)`.

`SyncRun` and `ProviderWrite` record the two directions of traffic: what a read attempt did, and what
a write is meant to do before it is attempted.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.models import BaseModel
from integrations.credentials import decrypt_credentials, encrypt_credentials
from integrations.providers.base import Capability
from integrations.registry import get_adapter_class


class ProviderConnection(BaseModel):
    """An organization's credentials for one external provider.

    Credentials are encrypted at rest. Read them through `get_credentials()`; never touch
    `credentials_encrypted` directly, and never log the result.
    """

    organization = models.ForeignKey(
        "events.Organization",
        on_delete=models.CASCADE,
        related_name="provider_connections",
    )

    slug = models.SlugField(
        max_length=100,
        help_text="Unique within the organization, e.g. 'pretalx-pyohio-2026'.",
    )
    name = models.CharField(max_length=200, help_text="Display name, e.g. 'PyOhio Pretalx (2026)'.")

    capability = models.CharField(max_length=32, choices=Capability.choices())
    provider = models.CharField(max_length=64, help_text="Registered provider name, e.g. 'pretalx'.")

    # Non-secret connection settings, e.g. a self-hosted provider's base URL.
    config = models.JSONField(default=dict, blank=True)

    # Fernet ciphertext of the credentials dict. Never exposed as readable text in the admin,
    # never included in logs, never meaningful in a dumpdata fixture.
    credentials_encrypted = models.TextField(blank=True, editable=False)

    is_active = models.BooleanField(default=True)
    last_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time these credentials were confirmed working.",
    )
    last_verify_error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="unique_connection_slug_per_org"),
        ]
        ordering = ["organization", "capability", "slug"]
        indexes = [models.Index(fields=["organization", "capability"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.capability}/{self.provider})"

    # --- Credentials --------------------------------------------------------

    def set_credentials(self, payload: dict) -> None:
        """Encrypt and store a credentials payload. Does not save."""
        self.credentials_encrypted = encrypt_credentials(payload)

    def get_credentials(self) -> dict:
        """Decrypt and return the credentials payload."""
        return decrypt_credentials(self.credentials_encrypted)

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials_encrypted)

    # --- Adapter -----------------------------------------------------------

    @property
    def adapter_class(self) -> type:
        return get_adapter_class(self.capability, self.provider)

    def missing_credential_keys(self) -> list[str]:
        """Required credential keys this connection has not been given."""
        credentials = self.get_credentials()
        return [
            key.name for key in self.adapter_class.credential_keys if key.required and not credentials.get(key.name)
        ]

    def mark_verified(self, *, error: str = "") -> None:
        """Record the outcome of a credential check."""
        if error:
            self.last_verify_error = error
        else:
            self.last_verified_at = timezone.now()
            self.last_verify_error = ""
        self.save(update_fields=["last_verified_at", "last_verify_error", "updated_at"])

    def clean(self):
        """Reject a provider name that no adapter is registered for."""
        super().clean()
        if self.provider and self.capability:
            try:
                get_adapter_class(self.capability, self.provider)
            except Exception as exc:
                raise ValidationError({"provider": str(exc)}) from exc


class EventProviderBinding(BaseModel):
    """Selects which connection an event uses for a capability, plus event-specific config.

    One binding per capability per event. Several sources for one capability (a main CFP plus
    separately-managed keynotes, say) is a real eventual need but complicates every sync path, so
    it is out of scope until something requires it.
    """

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="provider_bindings")
    capability = models.CharField(max_length=32, choices=Capability.choices())
    connection = models.ForeignKey(
        ProviderConnection,
        on_delete=models.PROTECT,
        related_name="bindings",
        help_text="Must belong to the event's organization and provide this capability.",
    )

    # Event-specific settings, e.g. {"event_id": "pyohio-2026"} or {"playlist_id": "PL..."}.
    config = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "capability"], name="unique_binding_per_event_capability"),
        ]
        ordering = ["event", "capability"]

    def __str__(self) -> str:
        return f"{self.event} {self.capability} -> {self.connection.slug}"

    def clean(self):
        """Validate what the database cannot.

        Neither of these can be a DB constraint without denormalizing the organization onto the
        binding, and both are mistakes that are easy to make in the admin.
        """
        super().clean()
        errors = {}

        if self.connection_id and self.event_id:
            if self.connection.organization_id != self.event.organization_id:
                errors["connection"] = (
                    "Connection belongs to a different organization than the event. "
                    "Credentials are never shared across organizations."
                )
            if self.capability and self.connection.capability != self.capability:
                errors["connection"] = (
                    f"Connection provides {self.connection.capability}, but this binding is for {self.capability}."
                )

        missing = self.missing_config_keys()
        if missing:
            errors["config"] = f"Missing required config keys for this provider: {', '.join(missing)}"

        if errors:
            raise ValidationError(errors)

    def missing_config_keys(self) -> list[str]:
        """Required event-level config keys this binding has not been given."""
        if not self.connection_id:
            return []
        try:
            adapter_class = self.connection.adapter_class
        except Exception:
            # An unregistered provider is reported by ProviderConnection.clean instead.
            return []
        return [key.name for key in adapter_class.event_config_keys if key.required and not self.config.get(key.name)]


class SyncRun(BaseModel):
    """Record of one sync attempt.

    The operational answer to "did the Pretalx pull actually work". This is the useful part of
    the legacy project's `SystemEvent` model, scoped down to syncs.
    """

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="sync_runs")
    capability = models.CharField(max_length=32, choices=Capability.choices())
    provider = models.CharField(max_length=64)

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    succeeded = models.BooleanField(default=False)
    error = models.TextField(blank=True)

    # Per-model counts, e.g. {"talks_created": 3, "talks_updated": 40, "missing": 1}.
    counts = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["event", "capability", "-started_at"])]

    def __str__(self) -> str:
        outcome = "ok" if self.succeeded else "failed"
        return f"{self.capability} sync for {self.event} ({outcome})"

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class ProviderWrite(BaseModel):
    """One intended change to a provider, durable before it is attempted.

    Reads and writes fail differently. A failed read costs a wait, and the upsert-and-never-delete
    rules mean a partial one cannot corrupt anything. A failed write leaves the database asserting
    something about the provider that is not true, and nothing detects it, because the only record of
    the intent was the request that failed.

    So: **local state records what the provider has confirmed, and intent lives here.** Publishing sets
    the desired state in this table, the write executes, the provider confirms, and only then does
    local state move. Divergence stops being something to reconcile because no code path produces it.

    The load-bearing property is that a row is committed in the same transaction as the local change
    that motivated it, so there is no window where a speaker's caption edit is saved but the intent to
    push it is lost. See plans/provider-writes.md.
    """

    class Operation(models.TextChoices):
        SET_PRIVACY = "set_privacy", "Set privacy"
        UPLOAD_CAPTIONS = "upload_captions", "Upload captions"

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_FLIGHT = "in_flight", "In flight"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"
        SUPERSEDED = "superseded", "Superseded"

    # Attempts before a write stops retrying and becomes visibly failed. Low, because at these volumes
    # a write that has failed three times needs a person, not a fourth attempt.
    MAX_ATTEMPTS = 3

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="provider_writes")
    capability = models.CharField(max_length=32, choices=Capability.choices())
    operation = models.CharField(max_length=32, choices=Operation.choices)

    target_external_id = models.CharField(max_length=128, help_text="The provider's id for the thing being written.")

    # The intended end state, never a delta. Retrying is then idempotent by construction, and a
    # superseded request can be discarded rather than replayed in order.
    desired = models.JSONField(default=dict)

    # What the provider returned once the write succeeded, e.g. a caption track id and the hash of the
    # content that was uploaded. Enough to tell later whether a further write is needed.
    result = models.JSONField(default=dict, blank=True)

    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)

    not_before = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Earliest this may be attempted. Set by a quota deferral or a scheduled release.",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_writes",
    )

    class Meta:
        constraints = [
            # One pending write per target and operation. A speaker editing captions three times needs
            # the latest content pushed once, not three uploads; a new request supersedes the pending
            # one rather than queueing behind it. Scoped to `pending` so history is kept and so a write
            # already in flight does not block recording fresher intent.
            #
            # The condition uses a literal because a nested class body cannot see names from the
            # enclosing one, so `State.PENDING` here would be a NameError.
            models.UniqueConstraint(
                fields=["event", "target_external_id", "operation"],
                condition=models.Q(state="pending"),
                name="one_pending_write_per_target_and_operation",
            ),
        ]
        ordering = ["created_at"]
        indexes = [models.Index(fields=["state", "not_before"])]

    def __str__(self) -> str:
        return f"{self.operation} {self.target_external_id} ({self.state})"

    @property
    def is_open(self) -> bool:
        """Still expected to happen, as opposed to settled."""
        return self.state in {self.State.PENDING, self.State.IN_FLIGHT}

    @property
    def attempts_remain(self) -> bool:
        return self.attempts < self.MAX_ATTEMPTS
