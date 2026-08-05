from django.apps import AppConfig


class VideosConfig(AppConfig):
    name = "videos"
    verbose_name = "Videos"

    def ready(self):
        # Importing the write handlers registers them with the outbox, so a queued write can always be
        # executed. Without this, `enqueue` would refuse and a drain would find rows it cannot run.
        from videos import writes  # noqa: F401
