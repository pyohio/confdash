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
    list_display = ["title", "event", "session_type", "duration_minutes", "state", "do_not_record"]
    list_filter = ["event", "session_type", "state", "do_not_record"]
    search_fields = ["title", "external_id"]
    autocomplete_fields = ["event"]
    readonly_fields = SYNCED_FIELDS
    inlines = [TalkSpeakerInline]


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
