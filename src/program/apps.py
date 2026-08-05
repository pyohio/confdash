from django.apps import AppConfig


class ProgramConfig(AppConfig):
    name = "program"
    verbose_name = "Program"

    def ready(self):
        # Importing the identity module connects the `user_logged_in` receiver that claims a user's
        # Speaker rows. Without this, logging in never makes anyone a speaker.
        from program import identity  # noqa: F401
