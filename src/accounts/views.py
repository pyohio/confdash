"""Passwordless login.

Three views: ask for a link, a confirmation page that says nothing, and an interstitial that consumes
the token.

**The token is consumed by POST, never by GET.** Mail security scanners and link previewers fetch URLs
in messages before the recipient sees them, and a single-use GET link is spent by the time it is
clicked. The result is a speaker reporting a broken link with no bug behind it. So the emailed URL
renders a page with a button, and the button's POST is what spends the token.

The request view never reveals whether an address is known. See `accounts.services`.
"""

import structlog
from django import forms
from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts import services, tokens
from accounts.auth_method import AuthMethod, set_auth_method

logger = structlog.get_logger(__name__)


class LoginRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"class": "input input-bordered w-full", "autofocus": True}),
    )


@require_http_methods(["GET", "POST"])
def request_link(request):
    """Ask for a sign-in link."""
    if request.method == "GET":
        return render(request, "accounts/request_link.html", {"form": LoginRequestForm()})

    form = LoginRequestForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/request_link.html", {"form": form}, status=400)

    services.send_login_link(
        form.cleaned_data["email"],
        next_url=tokens.safe_next_url(request.POST.get("next") or request.GET.get("next")),
        base_url=services.base_url_for(request),
    )

    # Always the same page, whatever happened. An unknown address, a throttled one, and a successful
    # send are indistinguishable here on purpose.
    return redirect(reverse("accounts:link_sent"))


def link_sent(request):
    return render(request, "accounts/link_sent.html")


@require_http_methods(["GET", "POST"])
def consume_link(request, token: str):
    """Interstitial for an emailed link, and the POST that spends it.

    The GET only checks that the token is currently usable, so a prefetch shows the page without
    burning it. The window between the two is the token's remaining lifetime, which is the point.
    """
    if request.method == "GET":
        usable = tokens.lookup(token) is not None
        return render(
            request,
            "accounts/consume_link.html",
            {"token": token, "usable": usable},
            status=200 if usable else 410,
        )

    consumed = tokens.consume(token)
    if consumed is None:
        # Same page as an expired GET: a used, expired, and never-real token must look alike.
        return render(request, "accounts/consume_link.html", {"token": token, "usable": False}, status=410)

    login(request, consumed.user)
    # Without this the session grants nothing: `permits_organizer_access` fails closed on a session with
    # no recorded mechanism, and a magic link must never confer organizer access anyway.
    set_auth_method(request, AuthMethod.MAGIC_LINK)

    logger.info("login.magic_link", user_id=str(consumed.user_id))

    destination = tokens.safe_next_url(consumed.next_url) or reverse("accounts:signed_in")
    return redirect(destination)


def signed_in(request):
    """Where a link with no destination lands: the user's own review list.

    Not a landing page of its own. A speaker following a link with no deep destination wants their
    videos, and an organizer arriving by magic link deliberately gets the same speaker view, since
    organizer screens need a federated session.
    """
    return redirect(reverse("review:my_reviews"))
