"""Organizer video screens.

The confirm queue is the first place the Django admin is the wrong tool: matching a playlist to a
programme wants a side-by-side with a ranked suggestion per video, not a changelist where every link is
a foreign-key dropdown over 31 talks.

Every mutation is a POST that returns the replaced row, so the page never re-renders wholesale and the
back button never replays a decision. The matcher only ever proposes; a human confirms, and
`videos.services` records who did.
"""

import structlog
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from events.decorators import organizer_view
from events.scopes import Scope
from program.models import Talk
from videos import matching
from videos.models import Video
from videos.services import link_video_to_talk, mark_standalone, unmatch

logger = structlog.get_logger(__name__)

# Suggestions offered per video. Enough to cover a near-miss without turning the row into a menu.
SUGGESTION_LIMIT = 4


@organizer_view(Scope.VIDEOS)
def match_queue(request, event):
    """Videos awaiting a matching decision, each with ranked suggestions.

    Talks are fetched once and scored in memory rather than queried per video: an event is a few dozen
    talks, and the alternative is a query per row for no benefit.
    """
    videos = list(Video.objects.filter(event=event, talk__isnull=True, standalone=False))
    talks = list(Talk.objects.filter(event=event))

    rows = [_row(video, talks) for video in videos]
    auto_acceptable = [row for row in rows if row["is_unambiguous"]]

    return render(
        request,
        "videos/match_queue.html",
        {
            "event": event,
            "rows": rows,
            "auto_acceptable_count": len(auto_acceptable),
            "settled": _settled(event),
            "talk_count": len(talks),
        },
    )


def _row(video: Video, talks: list[Talk]) -> dict:
    """One queue row: the video, its ranked suggestions, and whether it is safe to bulk-accept."""
    suggestions = matching.suggest(video.title, talks, limit=SUGGESTION_LIMIT)
    return {
        "video": video,
        "suggestions": suggestions,
        # High confidence alone is not enough: two talks in a series can both clear the threshold, so a
        # margin over the runner-up is what makes bulk acceptance a decision rather than a coin toss.
        "is_unambiguous": matching.is_unambiguous(suggestions),
    }


def _settled(event):
    """Videos already decided either way, so the page shows progress rather than only what is left."""
    return (
        Video.objects.filter(event=event)
        .exclude(talk__isnull=True, standalone=False)
        .select_related("talk")
        .order_by("title")
    )


@organizer_view(Scope.VIDEOS)
@require_POST
def confirm_match(request, event, video_id):
    """Link one video to the talk the organizer picked."""
    video = get_object_or_404(Video, pk=video_id, event=event)
    talk = get_object_or_404(Talk, pk=request.POST.get("talk"), event=event)

    link_video_to_talk(video, talk, user=request.user)

    return _replaced_row(request, event, video)


@organizer_view(Scope.VIDEOS)
@require_POST
def mark_video_standalone(request, event, video_id):
    """Record that a video deliberately has no talk: a welcome, closing remarks, a keynote."""
    video = get_object_or_404(Video, pk=video_id, event=event)

    mark_standalone(video, user=request.user)

    return _replaced_row(request, event, video)


@organizer_view(Scope.VIDEOS)
@require_POST
def undo_match(request, event, video_id):
    """Return a video to the queue, whichever way it was settled.

    The undo the plan calls for. It clears the audit stamps too, since leaving them would claim someone
    decided the state the video is now in, and that state is "undecided".
    """
    video = get_object_or_404(Video, pk=video_id, event=event)

    unmatch(video)

    talks = list(Talk.objects.filter(event=event))
    return render(request, "videos/partials/queue_row.html", {"event": event, "row": _row(video, talks)})


@organizer_view(Scope.VIDEOS)
@require_POST
def accept_suggested(request, event):
    """Accept every unambiguous suggestion at once.

    Only the unambiguous ones, and the ambiguous ones are deliberately left in the queue rather than
    matched to their top suggestion: a bulk action that guesses is worse than one that does less.
    """
    videos = Video.objects.filter(event=event, talk__isnull=True, standalone=False)
    talks = list(Talk.objects.filter(event=event))

    accepted = 0
    for video in videos:
        suggestions = matching.suggest(video.title, talks, limit=SUGGESTION_LIMIT)
        if not matching.is_unambiguous(suggestions):
            continue
        link_video_to_talk(video, suggestions[0].talk, user=request.user)
        accepted += 1

    logger.info("video.bulk_accepted", event_slug=event.slug, accepted=accepted)
    messages.success(request, f"Matched {accepted} video(s). Each one can still be undone individually.")

    # A full-page action, so a plain redirect rather than an HTMX swap: it changes most of the page, and
    # a redirect means a refresh afterwards cannot replay the bulk accept.
    return redirect(reverse("videos:match_queue", args=[event.organization.slug, event.slug]))


def _replaced_row(request, event, video: Video):
    """Render the settled row that replaces the queue row just acted on."""
    video.refresh_from_db()
    return render(request, "videos/partials/settled_row.html", {"event": event, "video": video})
