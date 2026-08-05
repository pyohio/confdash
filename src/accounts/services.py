"""Sending login links.

The whole surface is `send_login_link`, and its contract is that **the caller learns nothing about
whether the address exists**. An unknown address, a throttled address, and a successful send are
indistinguishable to the requester, because anything else turns the login form into a way to test
whether someone spoke at a conference.

That is also why throttling returns the same result as success rather than an error: a "too many
requests" response for known addresses only would leak exactly what the silence protects.
"""

from datetime import timedelta

import structlog
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts import tokens
from accounts.models import LoginToken, User

logger = structlog.get_logger(__name__)

# Per-address throttle. Generous enough for a speaker who mistypes their address twice and clicks an
# old link before asking again, tight enough that the form is not a mail cannon.
THROTTLE_WINDOW = timedelta(minutes=15)
THROTTLE_LIMIT = 5


def send_login_link(
    email: str,
    *,
    next_url: str = "",
    ttl: timedelta | None = None,
    base_url: str,
    subject: str = "Your sign-in link",
) -> bool:
    """Email a magic link if the address belongs to an active user.

    Returns whether a mail was actually sent. **Callers must not put that in a response**; it exists so
    a management command or an invitation flow can report what happened to an operator.

    `base_url` is passed in rather than derived here, because a link's host depends on the request or on
    the site being configured, and a service that guessed would produce links pointing at the wrong host
    in exactly the case that matters.
    """
    user = User.objects.filter(email__iexact=email.strip(), is_active=True).first()
    if user is None:
        logger.info("login_link.unknown_address")
        return False

    if _is_throttled(user):
        logger.warning("login_link.throttled", user_id=str(user.pk))
        return False

    raw = tokens.issue(user, ttl=ttl or tokens.SELF_SERVICE_TTL, next_url=next_url)
    # Reversed rather than formatted, so moving the URL cannot silently produce dead links in email.
    link = f"{base_url.rstrip('/')}{reverse('accounts:consume_link', args=[raw])}"

    send_mail(
        subject=subject,
        message=render_to_string("accounts/email/login_link.txt", {"user": user, "link": link}),
        # No from address: sender identity is per deployment, so mail defaults to DEFAULT_FROM_EMAIL.
        from_email=None,
        recipient_list=[user.email],
    )
    logger.info("login_link.sent", user_id=str(user.pk))
    return True


def _is_throttled(user: User) -> bool:
    """Whether this user has requested too many links recently.

    Counted on issued tokens rather than in a cache, because the tokens are already there and a cache is
    deliberately not part of this deployment yet. It means the window is per-address, not per-IP: the
    thing being protected is a person's inbox, and an attacker with many addresses to try is not
    something a per-address counter was ever going to stop.
    """
    since = timezone.now() - THROTTLE_WINDOW
    return LoginToken.objects.filter(user=user, created_at__gte=since).count() >= THROTTLE_LIMIT


def base_url_for(request) -> str:
    """The scheme and host to build links with.

    Taken from the request, which is correct in development and behind a proxy that sets
    `X-Forwarded-Proto`, and safe because `ALLOWED_HOSTS` has already rejected a forged Host header by
    the time a view runs.
    """
    return request.build_absolute_uri("/").rstrip("/")
