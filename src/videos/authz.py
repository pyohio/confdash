"""Who may review a video.

Two ways in, and they are not equivalent:

- **A speaker**, on the talks they are a speaker of, and nothing else. Magic-link session.
- **An organizer** holding `Scope.VIDEOS`, on everything in their organization. Federated session,
  because `events.authz` refuses organizer access to a magic-link session.

That second rule does real work here. An organizer who also gave a talk gets exactly their own talk
when they arrive on a magic link, and the whole event only when they log in through their
organization's identity provider. Holding a membership is not enough on its own.

Kept as predicates rather than decorators for the same reason as `events.authz`: the view decorator
needs the URL shape, which is still open, and the predicates are what it will wrap.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import HttpRequest

from events.authz import has_org_scope
from events.scopes import Scope
from videos.models import Video


def is_speaker_on(user, video: Video) -> bool:
    """Whether this user is a speaker on the video's talk.

    False for a standalone video: there is no talk, so there is no speaker with a claim to it. Those
    are staff-reviewed, which the organizer path covers.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if video.talk_id is None:
        return False
    return video.talk.speakers.filter(user=user).exists()


def speaker_videos(user, *, event=None) -> QuerySet[Video]:
    """Videos this user may review because they are a speaker on the talk.

    Filtered in the query rather than fetched and checked afterwards. Post-hoc ownership checks leak
    existence through timing and error differences, and they are easy to forget on one code path.
    """
    if not getattr(user, "is_authenticated", False):
        return Video.objects.none()

    queryset = Video.objects.filter(talk__speakers__user=user)
    if event is not None:
        queryset = queryset.filter(event=event)
    # distinct(), because a co-presented talk joins the speaker table more than once.
    return queryset.distinct()


def reviewable_videos(request: HttpRequest, event) -> QuerySet[Video]:
    """Every video in `event` this request may review.

    Everything for an organizer with the videos scope; only their own talks for a speaker.
    """
    if has_org_scope(request, event.organization, Scope.VIDEOS):
        return Video.objects.filter(event=event)
    return speaker_videos(request.user, event=event)


def may_review(request: HttpRequest, video: Video) -> bool:
    """Whether this request may open `video` for review."""
    if has_org_scope(request, video.event.organization, Scope.VIDEOS):
        return True
    return is_speaker_on(request.user, video)


def require_review_access(request: HttpRequest, video: Video) -> None:
    """Raise `PermissionDenied` unless this request may review `video`.

    Does not distinguish "not yours" from "no such video", so a speaker cannot enumerate an event's
    videos by probing for which ids produce which error.
    """
    if not may_review(request, video):
        raise PermissionDenied


def may_approve_as_speaker(user, video: Video) -> bool:
    """Whether an approval by this user counts as the speaker's own consent.

    The question `approved_by` cannot answer on its own, and the reason `approval_source` exists.
    """
    return is_speaker_on(user, video)
