"""Project-level smoke tests: the app boots, the healthcheck answers, the admin is reachable."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.integration


def test_healthz_returns_ok(client):
    """Backs the container HEALTHCHECK, so a regression here breaks deploys."""
    response = client.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_does_not_require_authentication(client):
    assert client.get("/healthz/").status_code == 200


def test_admin_redirects_anonymous_users_to_login(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_superuser_can_load_the_admin_index(client, django_user_model):
    """Also proves unfold's templates render, since it overrides the admin index."""
    django_user_model.objects.create_superuser(email="admin@example.org", password="admin-password")
    client.login(username="admin@example.org", password="admin-password")

    assert client.get("/admin/").status_code == 200
