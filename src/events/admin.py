from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from events.models import Event, Organization, OrganizationMembership


class OrganizationMembershipInline(TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ["user"]


class EventInline(TabularInline):
    model = Event
    extra = 0
    fields = ["name", "slug", "series", "start_date", "end_date", "is_active"]
    show_change_link = True


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ["name", "slug", "sender_address", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [OrganizationMembershipInline, EventInline]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = [
        (None, {"fields": ["name", "slug", "is_active"]}),
        (
            "Email",
            {
                "fields": ["from_email", "from_name"],
                "description": "Leave blank to use the deployment default sender. A custom domain "
                "must be verified with the configured email provider first.",
            },
        ),
        ("Settings", {"fields": ["settings"]}),
        ("Dates", {"fields": ["created_at", "updated_at"]}),
    ]

    @admin.display(description="Sends as")
    def sender_address(self, obj: Organization) -> str:
        return obj.sender_address()


@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ["name", "organization", "slug", "series", "start_date", "end_date", "is_active"]
    list_filter = ["organization", "series", "is_active"]
    search_fields = ["name", "slug", "series"]
    autocomplete_fields = ["organization"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(ModelAdmin):
    list_display = ["user", "organization", "role"]
    list_filter = ["role", "organization"]
    search_fields = ["user__email", "organization__name"]
    autocomplete_fields = ["user", "organization"]
