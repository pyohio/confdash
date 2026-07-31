"""Credential encryption tests.

These matter more than most: the app holds other organizations' API tokens, and the whole point
of the encryption layer is that a database dump does not leak them.
"""

import json

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from integrations.credentials import (
    CredentialDecryptionError,
    decrypt_credentials,
    encrypt_credentials,
)

pytestmark = pytest.mark.unit


def test_round_trip():
    payload = {"api_token": "secret-value", "api_base_url": "https://example.org"}
    assert decrypt_credentials(encrypt_credentials(payload)) == payload


def test_ciphertext_does_not_contain_the_secret():
    """The actual security property: the stored value must not reveal the plaintext."""
    ciphertext = encrypt_credentials({"api_token": "hunter2-is-the-token"})
    assert "hunter2" not in ciphertext
    assert "api_token" not in ciphertext


def test_empty_ciphertext_is_an_empty_dict():
    """A connection created before credentials are entered must not blow up on read."""
    assert decrypt_credentials("") == {}


def test_encryption_is_non_deterministic():
    """Fernet uses a random IV, so identical payloads must not produce identical ciphertext."""
    payload = {"api_token": "same"}
    assert encrypt_credentials(payload) != encrypt_credentials(payload)


def test_rejects_non_dict_payload():
    with pytest.raises(TypeError):
        encrypt_credentials("just-a-string")


def test_wrong_key_raises_a_typed_error():
    """A rotated or mismatched key must produce a clear error, not a raw InvalidToken."""
    ciphertext = encrypt_credentials({"api_token": "secret"})
    with override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode()):
        with pytest.raises(CredentialDecryptionError):
            decrypt_credentials(ciphertext)


def test_invalid_key_is_reported_as_misconfiguration():
    with override_settings(FIELD_ENCRYPTION_KEY="not-a-valid-fernet-key"):
        with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
            encrypt_credentials({"api_token": "secret"})


def test_nested_payload_survives():
    """Some providers need structured credentials, e.g. a full OAuth client blob."""
    payload = {"client_id": "id", "client_secret": "secret", "scopes": ["a", "b"]}
    assert decrypt_credentials(encrypt_credentials(payload)) == payload


def test_bytes_key_is_accepted():
    """settings may supply the key as bytes or str depending on how it was loaded."""
    key = Fernet.generate_key()
    with override_settings(FIELD_ENCRYPTION_KEY=key):
        assert decrypt_credentials(encrypt_credentials({"a": "b"})) == {"a": "b"}


def test_payload_is_json_serialized():
    """Guard the storage format, since a change here would strand existing rows."""
    key = Fernet.generate_key()
    with override_settings(FIELD_ENCRYPTION_KEY=key.decode()):
        ciphertext = encrypt_credentials({"b": 2, "a": 1})
    plaintext = Fernet(key).decrypt(ciphertext.encode())
    assert json.loads(plaintext) == {"a": 1, "b": 2}
