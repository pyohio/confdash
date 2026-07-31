from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from unfold.admin import ModelAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    """Email-keyed user admin.

    Passwordless in normal use, but the password fieldset stays so a superuser created for
    admin access can still be managed here.
    """

    list_display = ["email", "name", "is_active", "is_staff", "date_joined"]
    list_filter = ["is_active", "is_staff", "is_superuser"]
    search_fields = ["email", "name"]
    ordering = ["email"]
    readonly_fields = ["date_joined", "created_at", "updated_at"]

    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Profile", {"fields": ["name"]}),
        ("Permissions", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Dates", {"fields": ["last_login", "date_joined", "created_at", "updated_at"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "password1", "password2"]}),
    ]
