"""Email backend selection.

The provider is configuration, not a dependency: this deployment uses Postmark, but an
organization self-hosting confdash will have its own. These tests exercise the settings logic by
reimporting the settings module under different environments, since the branching happens at
import time.
"""

import importlib
import os
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.unit


@contextmanager
def settings_env(**overrides):
    """Reimport the settings module with the given environment applied.

    django-environ reads os.environ at import time, so overriding settings after the fact cannot
    exercise this branching. The module is reloaded again on exit so later tests see the real
    configuration.
    """
    saved = {key: os.environ.get(key) for key in overrides}
    os.environ.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
    try:
        import project.settings as settings_module

        yield importlib.reload(settings_module)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import project.settings as settings_module

        importlib.reload(settings_module)


def test_no_provider_falls_back_to_email_url():
    """How development reaches mailpit, and how a plain SMTP host would be configured."""
    with settings_env(DEBUG="True", EMAIL_PROVIDER=None, EMAIL_URL="smtp://mailpit:1025") as s:
        assert s.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
        assert s.EMAIL_HOST == "mailpit"
        assert s.EMAIL_PORT == 1025
        assert s.ANYMAIL == {}


def test_console_backend_is_the_default_with_no_configuration():
    with settings_env(DEBUG="True", EMAIL_PROVIDER=None, EMAIL_URL=None) as s:
        assert s.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"


def test_postmark_provider_selects_the_anymail_backend():
    with settings_env(DEBUG="True", EMAIL_PROVIDER="postmark", EMAIL_API_KEY="pm-test-token") as s:
        assert s.EMAIL_BACKEND == "anymail.backends.postmark.EmailBackend"
        assert s.ANYMAIL == {"POSTMARK_SERVER_TOKEN": "pm-test-token"}


def test_provider_name_is_case_insensitive():
    with settings_env(DEBUG="True", EMAIL_PROVIDER="Postmark", EMAIL_API_KEY="pm-test-token") as s:
        assert s.EMAIL_BACKEND == "anymail.backends.postmark.EmailBackend"


def test_another_provider_maps_to_its_own_credential_setting():
    """The point of the abstraction: swapping providers is an env change, not a code change."""
    with settings_env(DEBUG="True", EMAIL_PROVIDER="sendgrid", EMAIL_API_KEY="sg-test-key") as s:
        assert s.EMAIL_BACKEND == "anymail.backends.sendgrid.EmailBackend"
        assert s.ANYMAIL == {"SENDGRID_API_KEY": "sg-test-key"}


def test_credential_free_provider_needs_no_api_key():
    """Amazon SES authenticates through boto3, so requiring EMAIL_API_KEY would be wrong."""
    with settings_env(DEBUG="True", EMAIL_PROVIDER="amazon_ses", EMAIL_API_KEY=None) as s:
        assert s.EMAIL_BACKEND == "anymail.backends.amazon_ses.EmailBackend"
        assert s.ANYMAIL == {}


def test_missing_api_key_hard_fails_in_production():
    """Silently failing to send magic links would be worse than refusing to boot."""
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured, match="EMAIL_API_KEY"):
        with settings_env(
            DEBUG="False",
            SECRET_KEY="test-secret-key-long-enough-to-not-trip-the-deploy-check",
            FIELD_ENCRYPTION_KEY="cbrN3vfnpxYpMHRA_gN3dcbXWv-K5S8VQmM3ZzDcXtA=",
            EMAIL_PROVIDER="postmark",
            EMAIL_API_KEY=None,
        ):
            pass


def test_server_email_defaults_to_the_from_address():
    with settings_env(DEBUG="True", DEFAULT_FROM_EMAIL="confdash@confdash.org", SERVER_EMAIL=None) as s:
        assert s.SERVER_EMAIL == "confdash@confdash.org"


def test_empty_env_values_are_treated_as_unset():
    """An annotated .env carries commented-out keys as `KEY=`, and django-environ reads an empty
    value as set. Without an explicit `or`, that silently produces an empty sender."""
    with settings_env(DEBUG="True", DEFAULT_FROM_EMAIL="", SERVER_EMAIL="") as s:
        assert s.DEFAULT_FROM_EMAIL == "confdash@localhost"
        assert s.SERVER_EMAIL == "confdash@localhost"


def test_server_email_can_be_set_separately():
    """Operator-facing error mail need not come from the organization-facing address."""
    with settings_env(
        DEBUG="True",
        DEFAULT_FROM_EMAIL="speakers@confdash.org",
        SERVER_EMAIL="alerts@confdash.org",
    ) as s:
        assert s.SERVER_EMAIL == "alerts@confdash.org"
