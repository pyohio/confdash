"""Admin for provider connections, bindings, and the traffic they carry.

The organizer-facing job here is credential entry, which needs care: credentials must be
writable but never readable back. The form takes a write-only JSON payload and shows which keys
are currently stored, not their values.

`SyncRun` and `ProviderWrite` are operational records, so both are read-only: they are written by the
sync services and the outbox, and a hand-edited row would describe something that never happened.
"""

import json

from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline

from integrations.models import EventProviderBinding, ProviderConnection, ProviderWrite, SyncRun
from integrations.resolver import verify_connection


class ProviderConnectionForm(forms.ModelForm):
    """Write-only credential entry.

    Leaving the field blank keeps the stored credentials, so editing a connection's name does not
    require re-entering its token.
    """

    credentials_input = forms.CharField(
        label="Credentials (JSON)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "autocomplete": "off"}),
        help_text='Write-only. Leave blank to keep the stored credentials. Example: {"api_token": "..."}',
    )

    class Meta:
        model = ProviderConnection
        fields = ["organization", "slug", "name", "capability", "provider", "config", "is_active"]

    def clean_credentials_input(self):
        raw = self.cleaned_data.get("credentials_input", "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise forms.ValidationError('Credentials must be a JSON object, e.g. {"api_token": "..."}')
        return payload

    def save(self, commit=True):
        instance = super().save(commit=False)
        payload = self.cleaned_data.get("credentials_input")
        if payload is not None:
            instance.set_credentials(payload)
        if commit:
            instance.save()
        return instance


class EventProviderBindingInline(TabularInline):
    model = EventProviderBinding
    extra = 0
    fields = ["capability", "connection", "config", "is_active"]
    autocomplete_fields = ["connection"]


@admin.register(ProviderConnection)
class ProviderConnectionAdmin(ModelAdmin):
    form = ProviderConnectionForm
    list_display = [
        "name",
        "slug",
        "organization",
        "capability",
        "provider",
        "credential_status",
        "last_verified_at",
        "is_active",
    ]
    list_filter = ["organization", "capability", "provider", "is_active"]
    search_fields = ["name", "slug", "provider"]
    autocomplete_fields = ["organization"]
    readonly_fields = ["credential_status", "last_verified_at", "last_verify_error", "created_at", "updated_at"]
    actions = ["verify_credentials"]

    fieldsets = [
        (None, {"fields": ["organization", "slug", "name", "is_active"]}),
        ("Provider", {"fields": ["capability", "provider", "config"]}),
        (
            "Credentials",
            {
                "fields": ["credentials_input", "credential_status", "last_verified_at", "last_verify_error"],
                "description": "Stored encrypted. Values cannot be read back through the admin.",
            },
        ),
        ("Dates", {"fields": ["created_at", "updated_at"]}),
    ]

    @admin.display(description="Credentials")
    def credential_status(self, obj: ProviderConnection) -> str:
        """Which credential keys are stored, never their values."""
        if not obj.pk or not obj.has_credentials:
            return "not set"
        try:
            keys = sorted(obj.get_credentials().keys())
        except Exception as exc:
            return f"unreadable ({type(exc).__name__})"
        missing = obj.missing_credential_keys() if obj.provider else []
        status = f"set: {', '.join(keys)}"
        if missing:
            status += f" — missing: {', '.join(missing)}"
        return status

    @admin.action(description="Verify credentials against the provider")
    def verify_credentials(self, request, queryset):
        for connection in queryset:
            ok, error = verify_connection(connection)
            if ok:
                self.message_user(request, f"{connection.slug}: credentials OK", messages.SUCCESS)
            else:
                self.message_user(request, f"{connection.slug}: {error}", messages.ERROR)


@admin.register(EventProviderBinding)
class EventProviderBindingAdmin(ModelAdmin):
    list_display = ["event", "capability", "connection", "is_active"]
    list_filter = ["capability", "is_active", "event__organization"]
    search_fields = ["event__name", "connection__slug"]
    autocomplete_fields = ["event", "connection"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(SyncRun)
class SyncRunAdmin(ModelAdmin):
    list_display = ["event", "capability", "provider", "started_at", "duration_seconds", "succeeded"]
    list_filter = ["capability", "provider", "succeeded", "event"]
    search_fields = ["event__name", "provider"]
    readonly_fields = [
        "event",
        "capability",
        "provider",
        "started_at",
        "finished_at",
        "succeeded",
        "error",
        "counts",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request) -> bool:
        # Sync runs are written by sync services, never by hand.
        return False


@admin.register(ProviderWrite)
class ProviderWriteAdmin(ModelAdmin):
    """The write queue, read-only apart from being able to retry a failure.

    Read-only because editing `desired` by hand would let the admin assert an intent no local change
    motivated, which is the one thing this table exists to make impossible. Retrying is the exception:
    a write that failed for an operational reason should not need a new approval to try again.
    """

    list_display = ["event", "operation", "target_external_id", "state", "attempts", "not_before", "confirmed_at"]
    list_filter = ["state", "capability", "operation", "event"]
    search_fields = ["target_external_id", "event__name"]
    readonly_fields = [
        "event",
        "capability",
        "operation",
        "target_external_id",
        "desired",
        "result",
        "state",
        "attempts",
        "last_error",
        "not_before",
        "confirmed_at",
        "requested_by",
        "created_at",
        "updated_at",
    ]
    actions = ["retry"]

    def has_add_permission(self, request) -> bool:
        return False

    @admin.action(description="Retry failed writes")
    def retry(self, request, queryset):
        """Return failed writes to the queue with their attempt count cleared.

        Only failures: re-queueing a confirmed write would ask the provider to redo something it has
        already done, and a superseded one has been replaced by fresher intent on purpose.
        """
        eligible = queryset.filter(state=ProviderWrite.State.FAILED)
        count = eligible.update(
            state=ProviderWrite.State.PENDING,
            attempts=0,
            not_before=None,
            updated_at=timezone.now(),
        )
        skipped = queryset.count() - count

        self.message_user(request, f"{count} write(s) queued for retry.", messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"{skipped} skipped: only failed writes can be retried.",
                messages.WARNING,
            )
