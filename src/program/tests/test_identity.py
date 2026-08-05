"""Resolving which `Speaker` rows a `User` is.

This is the join that makes speaker authorization work at all, and it happens on a login signal, so the
tests drive it through real logins rather than by calling the function.
"""

import pytest

from accounts.models import User
from program.identity import link_speaker_records
from program.models import Speaker

pytestmark = pytest.mark.integration


def make_speaker(event, *, email, external_id="S1", user=None) -> Speaker:
    return Speaker.objects.create(event=event, external_id=external_id, name="A Speaker", email=email, user=user)


class TestLinking:
    def test_claims_a_matching_unclaimed_speaker(self, event, db):
        speaker = make_speaker(event, email="speaker@example.org")
        user = User.objects.create_user(email="speaker@example.org")

        assert link_speaker_records(user) == 1
        speaker.refresh_from_db()
        assert speaker.user == user

    def test_matches_case_insensitively(self, event, db):
        """Provider records are not normalized, and neither is what someone types into a login form."""
        speaker = make_speaker(event, email="Speaker@Example.ORG")
        user = User.objects.create_user(email="speaker@example.org")

        link_speaker_records(user)

        speaker.refresh_from_db()
        assert speaker.user == user

    def test_claims_across_every_event(self, event, organization, db):
        """The same person speaking in two years is two rows, and both are theirs."""
        from events.models import Event

        other_year = Event.objects.create(organization=organization, slug="2027", name="PyOhio 2027", timezone="UTC")
        make_speaker(event, email="speaker@example.org", external_id="S1")
        make_speaker(other_year, email="speaker@example.org", external_id="S2")
        user = User.objects.create_user(email="speaker@example.org")

        assert link_speaker_records(user) == 2

    def test_never_steals_a_claimed_speaker(self, event, db):
        """Two people sharing an address in provider records is a data problem, not a reassignment."""
        first = User.objects.create_user(email="first@example.org")
        speaker = make_speaker(event, email="shared@example.org", user=first)
        second = User.objects.create_user(email="shared@example.org")

        assert link_speaker_records(second) == 0
        speaker.refresh_from_db()
        assert speaker.user == first

    def test_leaves_other_people_alone(self, event, db):
        someone_else = make_speaker(event, email="other@example.org", external_id="S2")
        user = User.objects.create_user(email="speaker@example.org")

        link_speaker_records(user)

        someone_else.refresh_from_db()
        assert someone_else.user is None

    def test_a_user_who_is_not_a_speaker_links_nothing(self, event, db):
        make_speaker(event, email="speaker@example.org")
        user = User.objects.create_user(email="organizer@example.org")

        assert link_speaker_records(user) == 0

    def test_a_speaker_row_with_no_email_is_never_claimed(self, event, db):
        """Pretalx does not always expose a speaker's address, and blank must not match blank."""
        speaker = make_speaker(event, email="")
        user = User.objects.create_user(email="speaker@example.org")

        link_speaker_records(user)

        speaker.refresh_from_db()
        assert speaker.user is None

    def test_is_idempotent(self, event, db):
        make_speaker(event, email="speaker@example.org")
        user = User.objects.create_user(email="speaker@example.org")

        assert link_speaker_records(user) == 1
        assert link_speaker_records(user) == 0


class TestOnLogin:
    """Wired to `user_logged_in`, so every login path resolves identity without remembering to."""

    def test_a_magic_link_login_claims_the_speaker(self, client, event, db):
        from django.core import mail

        speaker = make_speaker(event, email="speaker@example.org")
        User.objects.create_user(email="speaker@example.org")

        client.post("/accounts/login/", {"email": "speaker@example.org"})
        import re

        link = re.search(r"(/accounts/link/\S+/)", mail.outbox[0].body).group(1)
        client.post(link)

        speaker.refresh_from_db()
        assert speaker.user is not None

    def test_any_login_mechanism_resolves_it(self, client, event, db):
        """`force_login` fires the same signal, standing in for the SSO path that does not exist yet."""
        speaker = make_speaker(event, email="speaker@example.org")
        user = User.objects.create_user(email="speaker@example.org")

        client.force_login(user)

        speaker.refresh_from_db()
        assert speaker.user == user
