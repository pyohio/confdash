"""Review URLs, mounted flat at `/review/`.

Flat and opaque on purpose: a speaker arrives from an emailed link and should not need to know an
organization slug, and a URL carrying no organization or event is nothing to enumerate. UUIDv7 primary
keys exist for exactly this. See plans/decisions.md.
"""

from django.urls import path

from videos import review_views

app_name = "review"

urlpatterns = [
    path("", review_views.my_reviews, name="my_reviews"),
    path("<uuid:video_id>/", review_views.review, name="review"),
    path("<uuid:video_id>/approve/", review_views.approve_video, name="approve"),
    path("<uuid:video_id>/changes/", review_views.request_video_changes, name="request_changes"),
]
