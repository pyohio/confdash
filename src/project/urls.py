"""Root URL configuration.

The admin is the organizer ops surface for now, so it lives at a predictable path. Feature URLs
get included here as apps grow view layers.
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path

from project.version import __version__


def healthz(_request):
    """Liveness probe for the container healthcheck and any load balancer.

    Deliberately does not touch the database: this answers "is the process serving requests",
    not "is every dependency healthy". A readiness check that includes the database can come
    when there is something that needs the distinction.
    """
    return JsonResponse({"status": "ok", "version": __version__})


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
]
