"""Magic-link token mechanics.

The security properties get explicit coverage because none of them is visible in the happy path: a
working login link looks identical whether or not the token is stored hashed, single-use, or expiring.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts import tokens
from accounts.models import LoginToken, User

pytestmark = pytest.mark.integration


@pytest.fixture
def speaker(db) -> User:
    return User.objects.create_user(email="speaker@example.org", name="A Speaker")


class TestIssuing:
    def test_the_raw_token_is_never_stored(self, speaker):
        """A database leak must not hand over working login links."""
        raw = tokens.issue(speaker)

        stored = LoginToken.objects.get()
        assert stored.token_hash != raw
        assert raw not in stored.token_hash
        assert stored.token_hash == tokens.hash_token(raw)

    def test_tokens_are_unguessable_and_unique(self, speaker):
        minted = {tokens.issue(speaker) for _ in range(5)}

        assert len(minted) == 5
        assert all(len(raw) > 32 for raw in minted)

    def test_the_two_lifetimes_differ(self, speaker):
        """A speaker acting on an invitation may take days; someone waiting on a form should not."""
        tokens.issue(speaker, ttl=tokens.INVITATION_TTL)
        invitation = LoginToken.objects.get()

        assert invitation.expires_at - timezone.now() > timedelta(days=6)
        assert tokens.SELF_SERVICE_TTL < timedelta(hours=1)


class TestConsuming:
    def test_a_fresh_token_is_usable(self, speaker):
        raw = tokens.issue(speaker)

        consumed = tokens.consume(raw)

        assert consumed is not None
        assert consumed.user == speaker

    def test_a_token_works_only_once(self, speaker):
        raw = tokens.issue(speaker)
        tokens.consume(raw)

        assert tokens.consume(raw) is None

    def test_an_expired_token_is_refused(self, speaker):
        raw = tokens.issue(speaker, ttl=timedelta(seconds=-1))

        assert tokens.consume(raw) is None

    def test_an_unknown_token_is_refused(self, speaker):
        assert tokens.consume("not-a-real-token") is None

    def test_an_empty_token_is_refused(self, speaker):
        assert tokens.consume("") is None

    def test_lookup_does_not_spend_the_token(self, speaker):
        """The GET interstitial checks usability, so a mail scanner's prefetch must not burn the link."""
        raw = tokens.issue(speaker)

        assert tokens.lookup(raw) is not None
        assert tokens.lookup(raw) is not None
        assert tokens.consume(raw) is not None

    def test_only_one_of_two_simultaneous_consumes_wins(self, speaker):
        """The database decides, not a read-then-write in Python."""
        raw = tokens.issue(speaker)
        token = tokens.lookup(raw)

        # Stand in for the racing request: it claimed the row between our lookup and our update.
        LoginToken.objects.filter(token_hash=token.token_hash).update(consumed_at=timezone.now())

        assert tokens.consume(raw) is None

    def test_consuming_one_token_leaves_others_alone(self, speaker):
        """Resending an invitation mints a fresh link; using it must not break the one in flight."""
        first = tokens.issue(speaker)
        second = tokens.issue(speaker)

        tokens.consume(second)

        assert tokens.lookup(first) is not None


class TestNextUrl:
    @pytest.mark.parametrize(
        "candidate",
        [
            "https://evil.example.com/phish",
            "//evil.example.com/phish",
            "http://localhost:8000/o/pyohio/2026/videos/",
        ],
    )
    def test_an_absolute_destination_is_dropped(self, speaker, candidate):
        """A token carrying an off-site redirect would be an open redirect signed by us."""
        raw = tokens.issue(speaker, next_url=candidate)

        assert LoginToken.objects.get().next_url == ""
        assert tokens.consume(raw).next_url == ""

    def test_a_relative_destination_survives(self, speaker):
        raw = tokens.issue(speaker, next_url="/o/pyohio/2026/videos/")

        assert tokens.consume(raw).next_url == "/o/pyohio/2026/videos/"

    def test_no_destination_is_fine(self, speaker):
        tokens.issue(speaker)
        assert LoginToken.objects.get().next_url == ""


class TestPurging:
    def test_removes_only_expired_tokens(self, speaker):
        tokens.issue(speaker, ttl=timedelta(seconds=-1))
        live = tokens.issue(speaker)

        removed = tokens.purge_expired()

        assert removed == 1
        assert tokens.lookup(live) is not None

    def test_a_consumed_but_unexpired_token_is_kept(self, speaker):
        """Housekeeping, not security: the consumed flag is what refuses it, and the row is the audit."""
        raw = tokens.issue(speaker)
        tokens.consume(raw)

        assert tokens.purge_expired() == 0
