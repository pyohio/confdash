"""The review surface, for speakers and for staff.

**One surface, two audiences**, which is the whole point rather than an economy. Some speakers ask us to
review their captions for them, so staff need to see exactly what the speaker would see. Two parallel
screens would drift, and the one that drifted would be the one used least.

URLs are flat and opaque: `/review/` and `/review/<uuid>/`. Speakers arrive from an emailed link and
should not have to learn an organization slug, and a URL carrying no org or event is nothing to
enumerate. Organizer *management* screens are path-scoped under `/o/`; this is not one of those.

Authorization is `videos.authz`, which resolves to the speaker's own talks or, for a federated organizer
holding `Scope.VIDEOS`, everything in their organization. An organizer who also gave a talk therefore
sees one video on a magic link and the whole event through their IdP, which is the intended asymmetry.
"""

import structlog
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from videos.authz import may_approve_as_speaker, require_review_access, speaker_videos
from videos.models import Video
from videos.services import approve, request_changes

logger = structlog.get_logger(__name__)


@login_required
def my_reviews(request):
    """Everything this user has to review as a speaker.

    Deliberately the speaker's own list only, even for an organizer: the organizer's whole-event view is
    the confirm queue under `/o/`. Mixing them here would mean an organizer's landing page buried their
    own talk among sixty others.
    """
    videos = speaker_videos(request.user).select_related("talk", "event").order_by("event", "title")
    return render(request, "videos/my_reviews.html", {"videos": videos})


@login_required
def review(request, video_id):
    """One video, with what is known about it and the actions available.

    No player yet: embedding is provider knowledge and no event has a `video_host` binding until M1.2, so
    a placeholder is honest and a hardcoded YouTube iframe would not be.
    """
    video = get_object_or_404(Video.objects.select_related("talk", "event", "event__organization"), pk=video_id)
    require_review_access(request, video)

    return render(
        request,
        "videos/review.html",
        {
            "video": video,
            "as_speaker": may_approve_as_speaker(request.user, video),
        },
    )


@require_POST
@login_required
def approve_video(request, video_id):
    """Record approval.

    `videos.services.approve` derives whether this counts as the speaker's own consent, rather than
    taking it as an argument, so no view can record a staff approval as a speaker's.
    """
    video = get_object_or_404(Video.objects.select_related("talk", "event"), pk=video_id)
    require_review_access(request, video)

    try:
        approve(video, user=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Thanks — approved. We will let you know when it is published.")

    return redirect(reverse("review:review", args=[video.pk]))


@require_POST
@login_required
def request_video_changes(request, video_id):
    """Send a video back, clearing any prior approval so it cannot publish on a stale one."""
    video = get_object_or_404(Video.objects.select_related("talk", "event"), pk=video_id)
    require_review_access(request, video)

    request_changes(video, user=request.user)
    messages.success(request, "Noted — we will take another look before publishing.")

    return redirect(reverse("review:review", args=[video.pk]))
