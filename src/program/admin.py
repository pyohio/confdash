"""Program admin.

Synced from a provider, so everything here is read-mostly: editing a title locally just means the
next sync overwrites it. The admin is for looking at what arrived and diagnosing a sync.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from program.models import Speaker, Talk, TalkSpeaker

SYNCED_FIELDS = ["external_id", "created_at", "updated_at"]


class TalkSpeakerInline(TabularInline):
    model = TalkSpeaker
    extra = 0
    autocomplete_fields = ["speaker"]


@admin.register(Talk)
class TalkAdmin(ModelAdmin):
    list_display = ["title", "event", "session_type", "duration_minutes", "state", "do_not_record", "video_count"]
    list_filter = ["event", "session_type", "state", "do_not_record"]
    search_fields = ["title", "external_id"]
    autocomplete_fields = ["event"]
    readonly_fields = SYNCED_FIELDS

    def get_inlines(self, request, obj):
        """The video inline only makes sense on a saved talk, since videos inherit its event."""
        from videos.admin import VideoInline

        return [TalkSpeakerInline, VideoInline] if obj else [TalkSpeakerInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("event").prefetch_related("videos")

    @admin.display(description="Videos")
    def video_count(self, obj: Talk) -> int:
        return obj.videos.count()

    def save_formset(self, request, form, formset, change):
        """Set `event` on videos added through the inline, and stamp the match.

        The inline hides `event` because it is never a choice: a video attached to a talk belongs to
        that talk's event by definition.
        """
        from videos.models import Video

        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, Video):
                instance.event = form.instance.event
                if instance.talk_id and instance.matched_by_id is None:
                    from django.utils import timezone

                    instance.matched_by = request.user
                    instance.matched_at = timezone.now()
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


@admin.register(Speaker)
class SpeakerAdmin(ModelAdmin):
    list_display = ["name", "event", "email", "user", "talk_count"]
    list_filter = ["event"]
    search_fields = ["name", "email", "external_id"]
    autocomplete_fields = ["event", "user"]
    readonly_fields = SYNCED_FIELDS

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("talks")

    @admin.display(description="Talks")
    def talk_count(self, obj: Speaker) -> int:
        return obj.talks.count()
