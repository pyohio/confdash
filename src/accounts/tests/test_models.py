"""User and login token tests.

Passwordless is the point, so the tests assert that users really have no usable password while
`createsuperuser` still produces an account that can log into the admin.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import LoginToken, User

pytestmark = pytest.mark.integration


class TestUser:
    def test_created_users_have_no_usable_password(self, db):
        user = User.objects.create_user(email="speaker@example.org")
        assert user.has_usable_password() is False

    def test_email_is_the_username_field(self, db):
        user = User.objects.create_user(email="speaker@example.org")
        assert user.get_username() == "speaker@example.org"
        assert User.USERNAME_FIELD == "email"

    def test_email_is_normalized(self, db):
        """The domain is lowercased, so the same address cannot register twice by case."""
        user = User.objects.create_user(email="Speaker@EXAMPLE.ORG")
        assert user.email == "Speaker@example.org"

    def test_email_is_unique(self, db):
        User.objects.create_user(email="speaker@example.org")
        with pytest.raises(IntegrityError):
            User.objects.create_user(email="speaker@example.org")

    def test_email_is_required(self, db):
        with pytest.raises(ValueError, match="email"):
            User.objects.create_user(email="")

    def test_superuser_can_still_have_a_password(self, db):
        """Admin access must not depend on outbound email working."""
        user = User.objects.create_superuser(email="admin@example.org", password="a-real-password")

        assert user.is_staff and user.is_superuser
        assert user.has_usable_password()
        assert user.check_password("a-real-password")

    def test_superuser_without_staff_is_rejected(self, db):
        with pytest.raises(ValueError, match="is_staff"):
            User.objects.create_superuser(email="admin@example.org", is_staff=False)

    def test_short_name_falls_back_to_the_email_local_part(self, db):
        assert User.objects.create_user(email="jane@example.org").get_short_name() == "jane"
        assert User.objects.create_user(email="j@example.org", name="Jane Doe").get_short_name() == "Jane Doe"

    def test_full_name_is_a_single_field(self, db):
        """One name field: provider APIs give one string, and splitting it is lossy."""
        user = User.objects.create_user(email="a@example.org", name="Ursula K. Le Guin")
        assert user.get_full_name() == "Ursula K. Le Guin"

    def test_data_defaults_to_an_empty_dict(self, db):
        """The preferences escape hatch: usable without a migration, and never None."""
        assert User.objects.create_user(email="a@example.org").data == {}

    def test_data_round_trips(self, db):
        user = User.objects.create_user(email="a@example.org", data={"digest": "weekly"})
        user.refresh_from_db()
        assert user.data == {"digest": "weekly"}


class TestLoginToken:
    def test_a_fresh_token_is_usable(self, user):
        token = LoginToken.objects.create(
            token_hash="a" * 64,
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert token.is_usable

    def test_an_expired_token_is_not_usable(self, user):
        token = LoginToken.objects.create(
            token_hash="b" * 64,
            user=user,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        assert token.is_usable is False

    def test_a_consumed_token_is_not_usable(self, user):
        """Single use: a link that has been followed must not work again."""
        token = LoginToken.objects.create(
            token_hash="c" * 64,
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
            consumed_at=timezone.now(),
        )
        assert token.is_usable is False

    def test_the_hash_is_the_primary_key(self, user):
        """Only a hash is stored, so a database leak does not yield working login links."""
        token = LoginToken.objects.create(
            token_hash="d" * 64,
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        assert token.pk == "d" * 64
