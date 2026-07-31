"""Django settings for confdash.

Single settings module. Environment differences come from environment variables and `if DEBUG`
branches, not from a settings package with per-environment files.

Secrets required in production hard-fail at startup when DEBUG=False. Insecure development
fallbacks exist only under DEBUG=True, so a fresh checkout runs with no configuration while a
misconfigured production deploy refuses to start rather than running with a known-bad key.
"""

import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# settings.py -> confdash/ -> src/ -> repo root
SRC_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = SRC_DIR.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env.bool("DEBUG", default=False)

# Sentinel rather than a separate test settings module: the test suite runs against the same
# settings production does, so a setting that only works under test settings cannot hide here.
TESTING = "pytest" in sys.modules


def required_secret(name: str, *, dev_fallback: str) -> str:
    """Return a secret, falling back to an obviously-insecure value only in development."""
    value = env.str(name, default="")
    if value:
        return value
    if DEBUG:
        return dev_fallback
    raise ImproperlyConfigured(f"{name} must be set when DEBUG=False.")


SECRET_KEY = required_secret("SECRET_KEY", dev_fallback="insecure-development-key-do-not-deploy")

# Encrypts ProviderConnection.credentials. Losing this key means losing every organization's
# stored provider credentials, so it is treated as a required production secret.
FIELD_ENCRYPTION_KEY = required_secret(
    "FIELD_ENCRYPTION_KEY",
    dev_fallback="Zx3n8Zc1zqCk7WpHhVYPfM9tKbLdRsQaJgEuXnT2oYA=",
)

ALLOWED_HOSTS = ["*"] if DEBUG else env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

SITE_BASE_URL = env.str("SITE_BASE_URL", default="http://localhost:8000")

INSTALLED_APPS = [
    # Must precede django.contrib.admin so unfold's templates win.
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "anymail",
    "django_typer",
    # `common` is deliberately absent: it holds plain Python and abstract models, not a Django
    # app. Abstract models do not require app registration.
    "accounts",
    "events",
    "integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"
ASGI_APPLICATION = "project.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://confdash:confdash@localhost:5432/confdash",
    )
}
# Reuse connections rather than reconnecting per request.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [p for p in [SRC_DIR / "static"] if p.exists()]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Manifest storage gives hashed filenames that can be cached indefinitely, but it
        # requires collectstatic to have run: any {% static %} tag raises without a manifest.
        # collectstatic runs at image build, so manifest storage is production-only while
        # development and the test suite serve files directly.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if (DEBUG or TESTING)
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# Serve from the source directories rather than STATIC_ROOT, which is unpopulated in development.
WHITENOISE_AUTOREFRESH = DEBUG or TESTING

# --- Email ------------------------------------------------------------------
#
# Speaker magic links are transactional mail, so deliverability matters. This deployment sends
# through Mailgun from confdash.org, but the provider is deliberately not baked in: an
# organization self-hosting confdash will have its own provider and its own sending domain.
#
# Sender identity is per deployment, not per organization. One instance sends as one address.
#
# Two modes:
#   EMAIL_PROVIDER set    -> that Anymail backend (mailgun, postmark, sendgrid, amazon_ses, ...)
#   EMAIL_PROVIDER unset  -> EMAIL_URL, which is how development reaches mailpit

EMAIL_PROVIDER = env.str("EMAIL_PROVIDER", default="").strip().lower()

# The settings key each provider expects its credential under. Anymail reads these from the
# ANYMAIL dict; providers absent from this map (Amazon SES uses boto3 credentials) need no key.
ANYMAIL_CREDENTIAL_SETTINGS = {
    "mailgun": "MAILGUN_API_KEY",
    "postmark": "POSTMARK_SERVER_TOKEN",
    "sendgrid": "SENDGRID_API_KEY",
    "brevo": "BREVO_API_KEY",
    "resend": "RESEND_API_KEY",
    "mailjet": "MAILJET_API_KEY",
    "sparkpost": "SPARKPOST_API_KEY",
}

# Mailgun addresses its send API per domain rather than per account. Left unset, Anymail derives
# the domain from the message's From address, which is correct whenever DEFAULT_FROM_EMAIL is on
# the Mailgun domain. Set it only when the two differ.
ANYMAIL_SENDER_DOMAIN_SETTINGS = {"mailgun": "MAILGUN_SENDER_DOMAIN"}

ANYMAIL: dict = {}

if EMAIL_PROVIDER:
    EMAIL_BACKEND = f"anymail.backends.{EMAIL_PROVIDER}.EmailBackend"

    credential_setting = ANYMAIL_CREDENTIAL_SETTINGS.get(EMAIL_PROVIDER)
    if credential_setting:
        api_key = env.str("EMAIL_API_KEY", default="")
        if not api_key and not DEBUG:
            raise ImproperlyConfigured(
                f"EMAIL_API_KEY must be set when EMAIL_PROVIDER={EMAIL_PROVIDER!r} and DEBUG=False."
            )
        if api_key:
            ANYMAIL[credential_setting] = api_key

    sender_domain_setting = ANYMAIL_SENDER_DOMAIN_SETTINGS.get(EMAIL_PROVIDER)
    sender_domain = env.str("EMAIL_SENDER_DOMAIN", default="")
    if sender_domain_setting and sender_domain:
        ANYMAIL[sender_domain_setting] = sender_domain

    # Non-default API endpoint, chiefly for providers with regional hosts: Mailgun's EU region is
    # https://api.eu.mailgun.net/v3, and sending EU-resident data to the US default would be wrong.
    api_url = env.str("EMAIL_API_URL", default="")
    if api_url:
        ANYMAIL[f"{EMAIL_PROVIDER.upper()}_API_URL"] = api_url
else:
    # consolemail:// by default, smtp://mailpit:1025 inside the compose stack.
    vars().update(env.email_url("EMAIL_URL", default="consolemail://"))

# The sender for all mail this deployment sends. Its domain must be verified with the configured
# provider or mail will be rejected.
#
# `or` rather than a bare default throughout: an annotated .env.example carries commented-out keys
# as `KEY=`, and django-environ treats an empty value as set, so a default alone would be skipped
# in favour of an empty string.
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="") or "confdash@localhost"

# Error mail from Django itself, which is operator-facing rather than organization-facing.
SERVER_EMAIL = env.str("SERVER_EMAIL", default="") or DEFAULT_FROM_EMAIL

# --- Security ---------------------------------------------------------------

if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# --- Logging ----------------------------------------------------------------

LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
# Structured JSON in production, human-readable in development.
LOG_FORMAT = env.str("LOG_FORMAT", default="console" if DEBUG else "json")

from project.logging import configure_structlog  # noqa: E402

LOGGING = configure_structlog(log_level=LOG_LEVEL, log_format=LOG_FORMAT)

# --- Error tracking ---------------------------------------------------------

SENTRY_DSN = env.str("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env.str("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        # Provider credentials and magic-link tokens must never leave the app.
        send_default_pii=False,
    )

# --- Admin ------------------------------------------------------------------

UNFOLD = {
    "SITE_TITLE": "confdash",
    "SITE_HEADER": "confdash",
    "SHOW_HISTORY": True,
}
