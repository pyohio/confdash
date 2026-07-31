"""Encryption for provider credentials at rest.

This app holds other organizations' API credentials, so they are encrypted in the database
rather than stored as plaintext JSON. Fernet (AES-128-CBC with an HMAC) from `cryptography`,
keyed by `settings.FIELD_ENCRYPTION_KEY`.

What this protects against: a database dump, a backup file, or a read-only SQL credential
leaking tenant secrets. What it does not protect against: an attacker with application-level
access, who can simply ask Django to decrypt. That is the expected and accepted limit.

Losing `FIELD_ENCRYPTION_KEY` means losing every stored credential, with no recovery. Rotation
needs a re-encrypt pass over every row; see plans/issues.md.
"""

import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class CredentialDecryptionError(Exception):
    """Stored credentials could not be decrypted, usually a wrong or rotated key."""


def _fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt_credentials(payload: dict) -> str:
    """Serialize and encrypt a credentials dict. Returns ciphertext safe to store as text."""
    if not isinstance(payload, dict):
        raise TypeError("Credentials payload must be a dict.")
    # sort_keys so that re-encrypting an unchanged payload is at least deterministic in input;
    # Fernet output still differs each time because of the random IV.
    plaintext = json.dumps(payload, sort_keys=True).encode()
    return _fernet().encrypt(plaintext).decode()


def decrypt_credentials(ciphertext: str) -> dict:
    """Decrypt and deserialize a credentials payload. Empty input yields an empty dict."""
    if not ciphertext:
        return {}
    try:
        plaintext = _fernet().decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise CredentialDecryptionError(
            "Could not decrypt stored credentials. FIELD_ENCRYPTION_KEY may have changed."
        ) from exc
    return json.loads(plaintext)
