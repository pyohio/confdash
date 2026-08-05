"""Connecting a `User` to the `Speaker` rows that are them.

Sync creates `Speaker` rows from the provider with an email and no user, because the person may never
have logged in. Login creates a `User` with an email and no idea which speakers they are. This closes
that gap, by email, at every login.

**Wired to `user_logged_in` rather than called from a login view.** There will be more than one way in:
magic link today, organizer SSO next, an operator password already. A speaker who is also an organizer
must get their own talks whichever way they arrived, and a resolution step that each login path has to
remember to call is one that a future path will forget. This is the case a signal is actually for.

Email is the only join available. Provider speaker codes are per-event and per-provider, and nothing
else in a `SpeakerRecord` identifies a person. That makes this as trustworthy as the provider's email
verification, which is why it grants only speaker access to that speaker's own talks, never organizer
access: `accounts.auth_method` refuses that to a magic-link session regardless.
"""

import structlog
from django.contrib.auth.signals import user_logged_in
from django.db.models import Q
from django.dispatch import receiver

from program.models import Speaker

logger = structlog.get_logger(__name__)


def link_speaker_records(user) -> int:
    """Claim every unclaimed `Speaker` row matching this user's email. Returns how many.

    Only rows with no user are claimed. A `Speaker` already pointing at someone else is left alone: two
    people sharing an address in a provider's records is a data problem to look at, not something to
    resolve by reassignment, and reassigning would hand one person another's talk.
    """
    if not user or not getattr(user, "email", ""):
        return 0

    claimed = Speaker.objects.filter(Q(user__isnull=True), email__iexact=user.email).update(user=user)
    if claimed:
        logger.info("speaker.linked_to_user", user_id=str(user.pk), speakers=claimed)
    return claimed


@receiver(user_logged_in)
def link_on_login(sender, user, request, **kwargs):
    """Resolve speaker identity at every login, whatever the mechanism.

    Cheap enough not to think about: one UPDATE over a table that holds a few dozen rows per event, and
    it matches nothing at all for the common case of a user who is not a speaker. Deliberately not
    indexed for this: a case-insensitive match needs a functional index, which is not worth carrying for
    a query this size.
    """
    link_speaker_records(user)
