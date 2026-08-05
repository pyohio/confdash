"""Login URLs, mounted at `/accounts/`, which is what `settings.LOGIN_URL` points at.

The token is a path segment rather than a query parameter so it does not end up in a `Referer` header or
a proxy access log alongside the page it was used on.

`link/<token>/` rather than `login/<token>/` so no fixed page can ever be shadowed by a token that
happens to spell it. Resolution order would decide that, and relying on ordering for correctness is the
kind of thing that survives until someone reorders the list.
"""

from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.request_link, name="request_link"),
    path("sent/", views.link_sent, name="link_sent"),
    path("link/<str:token>/", views.consume_link, name="consume_link"),
    path("signed-in/", views.signed_in, name="signed_in"),
]
