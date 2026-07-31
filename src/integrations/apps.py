from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integrations"

    def ready(self):
        # Importing the providers package registers every adapter, so the registry is populated
        # before any request or management command runs.
        from integrations import providers  # noqa: F401
