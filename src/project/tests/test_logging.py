"""Log redaction tests.

The app holds other organizations' API credentials. Redaction lives in the logging pipeline
rather than at each call site, because relying on every call site to remember is exactly how
tokens end up in a log aggregator. These tests are the guarantee.
"""

import pytest

from project.logging import REDACTED, redact_sensitive

pytestmark = pytest.mark.unit


def redact(event_dict: dict) -> dict:
    return redact_sensitive(None, "info", event_dict)


def test_top_level_secret_is_redacted():
    assert redact({"event": "sync", "api_token": "secret"})["api_token"] == REDACTED


def test_non_sensitive_values_are_untouched():
    result = redact({"event": "sync", "event_slug": "2026", "talks": 42})
    assert result == {"event": "sync", "event_slug": "2026", "talks": 42}


def test_nested_secret_is_redacted():
    """The realistic accident: logging a whole config or credentials dict."""
    result = redact({"event": "resolve", "connection": {"slug": "pretalx-2026", "api_token": "secret"}})

    assert result["connection"]["api_token"] == REDACTED
    assert result["connection"]["slug"] == "pretalx-2026"


def test_secret_inside_a_list_is_redacted():
    result = redact({"connections": [{"api_token": "one"}, {"api_token": "two"}]})
    assert [c["api_token"] for c in result["connections"]] == [REDACTED, REDACTED]


def test_key_matching_is_case_insensitive():
    assert redact({"API_Token": "secret"})["API_Token"] == REDACTED


def test_whole_credentials_payload_is_redacted():
    """`credentials` is itself a sensitive key, so the dict is replaced wholesale."""
    assert redact({"credentials": {"anything": "here"}})["credentials"] == REDACTED


def test_authorization_header_is_redacted():
    assert redact({"headers": {"Authorization": "Token abc123"}})["headers"]["Authorization"] == REDACTED


def test_oauth_fields_are_redacted():
    result = redact({"client_secret": "s", "refresh_token": "r", "client_id": "public-id"})

    assert result["client_secret"] == REDACTED
    assert result["refresh_token"] == REDACTED
    # A client ID is not a secret, so redacting it would only make debugging harder.
    assert result["client_id"] == "public-id"


def test_magic_link_token_hash_is_redacted():
    assert redact({"token_hash": "abc"})["token_hash"] == REDACTED


def test_tuples_keep_their_type():
    result = redact({"items": ({"token": "a"},)})
    assert isinstance(result["items"], tuple)
    assert result["items"][0]["token"] == REDACTED
