"""Minting and consuming magic-link tokens.

Three properties, all of them the reason this is a module rather than a few lines in a view:

- **Only a hash is stored.** A database leak must not hand over working login links, so the raw token
  exists exactly long enough to be put in an email and is never persisted or logged.
- **Single use.** Consuming marks the row, and a second attempt fails even inside the expiry window.
- **Two lifetimes.** An invitation-borne link lasts about a week, because speakers act on their own
  schedule. A self-service "email me a link" lasts minutes. Same mechanism, different expiry at mint.

`next_url` is validated on the way in and on the way out. A token carrying an absolute URL would be an
open redirect signed by us, which is worse than an ordinary one because the link looks legitimate.
"""

import hashlib
import secrets
from datetime import timedelta

import structlog
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import LoginToken

logger = structlog.get_logger(__name__)

# 32 bytes of urlsafe base64. Long enough that guessing is not a threat model worth reasoning about.
TOKEN_BYTES = 32

# A speaker acting on an invitation may take days to get to it.
INVITATION_TTL = timedelta(days=7)

# A link someone just asked for should be short-lived: they are waiting on it now.
SELF_SERVICE_TTL = timedelta(minutes=15)


def hash_token(raw: str) -> str:
    """The stored form of a token.

    Plain SHA-256, deliberately not a password hash. A password hash is slow to resist offline guessing
    of a low-entropy secret; this is 256 bits of randomness with a lifetime measured in minutes, where
    the only thing that matters is that the stored form is not usable as a credential. Slowing it down
    would buy nothing and make every login link a CPU cost.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_next_url(candidate: str | None) -> str:
    """A relative path we are willing to redirect to, or empty.

    Rejects anything with a host or scheme rather than trying to allow-list hosts: every legitimate
    destination is inside this app, so a candidate naming a host is either a mistake or an attack.
    """
    if not candidate:
        return ""
    if not url_has_allowed_host_and_scheme(candidate, allowed_hosts=None):
        return ""
    return candidate


def issue(user, *, ttl: timedelta = SELF_SERVICE_TTL, next_url: str = "") -> str:
    """Create a token for `user` and return the raw value, which is never stored.

    The caller is responsible for putting it in an email and then forgetting it.
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    LoginToken.objects.create(
        token_hash=hash_token(raw),
        user=user,
        expires_at=timezone.now() + ttl,
        next_url=safe_next_url(next_url),
    )
    logger.info("login_token.issued", user_id=str(user.pk), ttl_seconds=int(ttl.total_seconds()))
    return raw


def lookup(raw: str) -> LoginToken | None:
    """The usable token matching `raw`, or None.

    None covers every failure identically: no such token, already consumed, expired. A caller must not
    be able to tell those apart, since the difference would say whether a link was ever real.
    """
    if not raw:
        return None
    token = LoginToken.objects.select_related("user").filter(token_hash=hash_token(raw)).first()
    if token is None or not token.is_usable:
        return None
    return token


def consume(raw: str) -> LoginToken | None:
    """Spend a token, returning it, or None if it was not usable.

    The update is conditional on the token still being unconsumed, so two simultaneous requests cannot
    both succeed: the database decides, not a read-then-write in Python.
    """
    token = lookup(raw)
    if token is None:
        return None

    now = timezone.now()
    claimed = LoginToken.objects.filter(token_hash=token.token_hash, consumed_at__isnull=True).update(consumed_at=now)
    if not claimed:
        return None

    token.consumed_at = now
    logger.info("login_token.consumed", user_id=str(token.user_id))
    return token


def purge_expired(*, before=None) -> int:
    """Delete tokens that can no longer be used.

    Housekeeping rather than security: an expired token is already refused. Worth having because these
    rows accumulate one per invitation per resend and nothing else would ever remove them.
    """
    cutoff = before or timezone.now()
    deleted, _ = LoginToken.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
