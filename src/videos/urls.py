"""Organizer video URLs, mounted under `/o/<organization_slug>/<event_slug>/videos/`.

The two slugs come from the prefix in `project/urls.py` and are consumed by `organizer_view`, so no
pattern here repeats them and no view signature carries them.
"""

from django.urls import path

from videos import views

app_name = "videos"

urlpatterns = [
    path("", views.match_queue, name="match_queue"),
    path("accept-suggested/", views.accept_suggested, name="accept_suggested"),
    path("<uuid:video_id>/match/", views.confirm_match, name="confirm_match"),
    path("<uuid:video_id>/standalone/", views.mark_video_standalone, name="mark_standalone"),
    path("<uuid:video_id>/undo/", views.undo_match, name="undo_match"),
]
