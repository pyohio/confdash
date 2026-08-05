"""Root URL configuration.

Organizer URLs are path-scoped under `/o/<organization_slug>/<event_slug>/`, so the tenant is resolved
and authorized by `events.decorators.organizer_view` before any view body runs. The `/o/` prefix keeps
the root free for `admin/`, `healthz/`, and static, and leaves room for sibling namespaces later.

Speaker URLs will be flat and opaque (`/review/<uuid>/`), reached from an emailed link: a speaker should
not have to learn an organization slug. See plans/decisions.md.
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from project.version import __version__


def healthz(_request):
    """Liveness probe for the container healthcheck and any load balancer.

    Deliberately does not touch the database: this answers "is the process serving requests",
    not "is every dependency healthy". A readiness check that includes the database can come
    when there is something that needs the distinction.
    """
    return JsonResponse({"status": "ok", "version": __version__})


ORGANIZER_PREFIX = "o/<slug:organization_slug>/<slug:event_slug>/"

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path(f"{ORGANIZER_PREFIX}videos/", include("videos.urls")),
]
