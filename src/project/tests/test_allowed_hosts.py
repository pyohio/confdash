"""ALLOWED_HOSTS composition.

The container healthcheck probes the app over loopback, so these guard against a deploy where every
health probe fails with DisallowedHost while the app serves real traffic normally.
"""

import pytest

from project.tests.helpers import settings_env

pytestmark = pytest.mark.unit

PROD_ENV = {
    "DEBUG": "False",
    "SECRET_KEY": "test-secret-key-long-enough-to-not-trip-the-deploy-check",
    "FIELD_ENCRYPTION_KEY": "cbrN3vfnpxYpMHRA_gN3dcbXWv-K5S8VQmM3ZzDcXtA=",
}


def test_debug_allows_any_host():
    with settings_env(DEBUG="True") as s:
        assert s.ALLOWED_HOSTS == ["*"]


def test_production_keeps_the_configured_hosts():
    with settings_env(**PROD_ENV, ALLOWED_HOSTS="confdash.org,www.confdash.org") as s:
        assert s.ALLOWED_HOSTS[:2] == ["confdash.org", "www.confdash.org"]


def test_loopback_is_always_allowed_in_production():
    """Without this the container healthcheck fails on every probe once DEBUG is off."""
    with settings_env(**PROD_ENV, ALLOWED_HOSTS="confdash.org") as s:
        assert "localhost" in s.ALLOWED_HOSTS
        assert "127.0.0.1" in s.ALLOWED_HOSTS


def test_loopback_is_not_duplicated_when_configured_explicitly():
    with settings_env(**PROD_ENV, ALLOWED_HOSTS="confdash.org,localhost") as s:
        assert s.ALLOWED_HOSTS.count("localhost") == 1


def test_loopback_alone_is_enough_with_nothing_configured():
    """A misconfigured deploy should still report healthy or unhealthy honestly, not 400 on itself."""
    with settings_env(**PROD_ENV, ALLOWED_HOSTS=None) as s:
        assert "localhost" in s.ALLOWED_HOSTS


class TestAgainstTheRealProbe:
    """Ties the computed hosts to the Host header the container healthcheck actually sends.

    `Dockerfile` probes `http://localhost:8000/healthz/`, so curl sends `Host: localhost:8000`.
    Asserting on the settings list alone would not catch the two drifting apart.
    """

    def test_the_probe_host_is_accepted_under_production_settings(self, client, settings):
        with settings_env(**PROD_ENV, ALLOWED_HOSTS="confdash.org") as s:
            computed = s.ALLOWED_HOSTS

        settings.DEBUG = False
        settings.ALLOWED_HOSTS = computed

        assert client.get("/healthz/", headers={"host": "localhost:8000"}).status_code == 200

    def test_and_would_be_rejected_without_the_loopback_entry(self, client, settings):
        """The negative control: this is the failure the loopback entry exists to prevent."""
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["confdash.org"]

        assert client.get("/healthz/", headers={"host": "localhost:8000"}).status_code == 400
