"""Video admin, and the manual linking path.

This is the floor for matching: paste a video reference against a talk and it is linked, with no
YouTube integration required. The HTMX confirm queue in M1.3 makes that faster for a whole playlist;
it does not replace it, because manual entry is what still works when a provider is misconfigured.
"""

from django import forms
from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from videos.models import Video
from videos.services import normalize_reference


class VideoAdminForm(forms.ModelForm):
    class Meta:
        model = Video
        fields = "__all__"

    def clean(self):
        """Accept a pasted URL wherever a video id is wanted.

        Normalizing here rather than in `Video.clean` keeps provider-URL handling out of the model:
        the model stores an id, and turning a reference into one is an input concern.
        """
        cleaned = super().clean()
        reference = cleaned.get("external_id")
        event = cleaned.get("event") or getattr(self.instance, "event", None)

        if reference and event:
            try:
                cleaned["external_id"] = normalize_reference(event, reference)
            except ValueError as exc:
                self.add_error("external_id", str(exc))

        return cleaned


class VideoInline(TabularInline):
    """Videos on a talk, so an organizer can link one without leaving the talk."""

    model = Video
    form = VideoAdminForm
    extra = 0
    fields = ["external_id", "title", "privacy_status", "review_state", "publication_state"]
    readonly_fields = ["privacy_status", "publication_state"]
    show_change_link = True

    def get_formset(self, request, obj=None, **kwargs):
        """Default `event` from the parent talk, so it is not a field an organizer can get wrong."""
        formset = super().get_formset(request, obj, **kwargs)
        if obj is not None:
            formset.form.base_fields.pop("event", None)
        return formset


@admin.register(Video)
class VideoAdmin(ModelAdmin):
    form = VideoAdminForm

    list_display = ["__str__", "event", "talk", "matching_state", "privacy_status", "review_state"]
    list_filter = ["event", "review_state", "publication_state", "privacy_status", "standalone"]
    search_fields = ["title", "external_id", "talk__title"]
    autocomplete_fields = ["event", "talk", "matched_by", "approved_by"]
    readonly_fields = ["created_at", "updated_at", "matched_at", "approved_at"]
    actions = ["action_mark_standalone", "action_unmatch"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("event", "talk")

    def save_model(self, request, obj, form, change):
        """Stamp the matching audit fields when a talk is set or changed here.

        Linking through this form is a match like any other, so it has to record who did it. Without
        this, only the talk inline stamped, and the audit trail depended on which screen was used.
        """
        from django.utils import timezone

        talk_changed = "talk" in form.changed_data if change else bool(obj.talk_id)
        if obj.talk_id and talk_changed:
            obj.matched_by = request.user
            obj.matched_at = timezone.now()
        elif not obj.talk_id and not obj.standalone:
            obj.matched_by = None
            obj.matched_at = None

        super().save_model(request, obj, form, change)

    @admin.display(description="Matching")
    def matching_state(self, obj: Video) -> str:
        if obj.talk_id:
            return "Matched"
        return "Standalone (staff review)" if obj.standalone else "Needs matching"

    @admin.action(description="Mark as standalone (staff reviews it)")
    def action_mark_standalone(self, request, queryset):
        from videos.services import mark_standalone

        for video in queryset:
            mark_standalone(video, user=request.user)
        self.message_user(
            request, f"Marked {queryset.count()} video(s) standalone; staff review applies.", messages.SUCCESS
        )

    @admin.action(description="Return to the matching queue")
    def action_unmatch(self, request, queryset):
        from videos.services import unmatch

        for video in queryset:
            unmatch(video)
        self.message_user(request, f"Returned {queryset.count()} video(s) to the queue.", messages.SUCCESS)
