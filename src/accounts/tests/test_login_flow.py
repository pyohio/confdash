"""The magic-link flow end to end, through real URLs, templates, and outbound mail.

Two behaviours carry most of the weight and are easy to regress:

- **No user-existence disclosure.** A known address, an unknown one, and a throttled one must be
  indistinguishable in the response.
- **GET does not consume.** Mail scanners prefetch links, and a single-use GET is spent before the
  recipient clicks.
"""

import re

import pytest
from django.core import mail

from accounts import services, tokens
from accounts.auth_method import SESSION_KEY, AuthMethod
from accounts.models import LoginToken, User

pytestmark = pytest.mark.integration

LOGIN = "/accounts/login/"


@pytest.fixture
def speaker(db) -> User:
    return User.objects.create_user(email="speaker@example.org", name="A Speaker")


def link_from_mail() -> str:
    """The path from the one mail that was sent, as the recipient would follow it."""
    assert len(mail.outbox) == 1
    found = re.search(r"(/accounts/link/\S+/)", mail.outbox[0].body)
    assert found, mail.outbox[0].body
    return found.group(1)


class TestRequestingALink:
    def test_a_known_address_gets_a_mail(self, client, speaker):
        response = client.post(LOGIN, {"email": "speaker@example.org"})

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["speaker@example.org"]

    def test_the_mail_carries_a_working_link(self, client, speaker):
        client.post(LOGIN, {"email": "speaker@example.org"})

        assert client.get(link_from_mail()).status_code == 200

    def test_the_raw_token_is_not_in_the_database(self, client, speaker):
        client.post(LOGIN, {"email": "speaker@example.org"})
        path = link_from_mail()
        raw = path.rstrip("/").rsplit("/", 1)[-1]

        assert LoginToken.objects.get().token_hash == tokens.hash_token(raw)

    def test_an_unknown_address_looks_identical(self, client, speaker):
        """Otherwise the form tells an outsider who spoke at a conference."""
        known = client.post(LOGIN, {"email": "speaker@example.org"})
        mail.outbox.clear()
        unknown = client.post(LOGIN, {"email": "nobody@example.org"})

        assert (known.status_code, known["Location"]) == (unknown.status_code, unknown["Location"])
        assert mail.outbox == []

    def test_an_inactive_user_gets_nothing_and_looks_the_same(self, client, speaker):
        speaker.is_active = False
        speaker.save()

        response = client.post(LOGIN, {"email": "speaker@example.org"})

        assert response.status_code == 302
        assert mail.outbox == []

    def test_the_address_is_matched_case_insensitively(self, client, speaker):
        client.post(LOGIN, {"email": "SPEAKER@Example.ORG"})

        assert len(mail.outbox) == 1

    def test_a_malformed_address_is_rejected_by_the_form(self, client, speaker):
        response = client.post(LOGIN, {"email": "not-an-email"})

        assert response.status_code == 400
        assert mail.outbox == []

    def test_throttling_is_silent(self, client, speaker):
        """A "too many requests" page for known addresses only would leak what the silence protects."""
        for _ in range(services.THROTTLE_LIMIT):
            client.post(LOGIN, {"email": "speaker@example.org"})
        mail.outbox.clear()

        response = client.post(LOGIN, {"email": "speaker@example.org"})

        assert response.status_code == 302
        assert mail.outbox == []

    def test_the_form_renders(self, client, db):
        response = client.get(LOGIN)

        assert response.status_code == 200
        assert "Email me a link" in response.content.decode()


class TestConsumingALink:
    def test_a_get_does_not_spend_the_token(self, client, speaker):
        """The whole reason for the interstitial: a scanner's prefetch must leave the link working."""
        client.post(LOGIN, {"email": "speaker@example.org"})
        path = link_from_mail()

        client.get(path)
        client.get(path)

        assert LoginToken.objects.get().consumed_at is None
        assert "_auth_user_id" not in client.session

    def test_the_post_signs_the_user_in(self, client, speaker):
        client.post(LOGIN, {"email": "speaker@example.org"})
        path = link_from_mail()
        client.get(path)

        response = client.post(path)

        assert response.status_code == 302
        assert client.session["_auth_user_id"] == str(speaker.pk)

    def test_the_session_records_that_it_was_a_magic_link(self, client, speaker):
        """Without this the session grants nothing, and it must never grant organizer access."""
        client.post(LOGIN, {"email": "speaker@example.org"})

        client.post(link_from_mail())

        assert client.session[SESSION_KEY] == str(AuthMethod.MAGIC_LINK)

    def test_a_magic_link_session_cannot_reach_an_organizer_url(self, client, speaker, event, organization):
        """The invariant, exercised through both real flows rather than asserted about a predicate."""
        from events.models import OrganizationMembership

        OrganizationMembership.objects.create(
            organization=organization, user=speaker, role=OrganizationMembership.Role.OWNER
        )
        client.post(LOGIN, {"email": "speaker@example.org"})
        client.post(link_from_mail())

        response = client.get(f"/o/{organization.slug}/{event.slug}/videos/")

        assert response.status_code == 403

    def test_a_second_post_fails(self, client, speaker):
        client.post(LOGIN, {"email": "speaker@example.org"})
        path = link_from_mail()
        client.post(path)
        client.logout()

        response = client.post(path)

        assert response.status_code == 410
        assert "_auth_user_id" not in client.session

    def test_an_unknown_token_looks_like_an_expired_one(self, client, speaker):
        """A used, expired, and never-real token must be indistinguishable."""
        client.post(LOGIN, {"email": "speaker@example.org"})
        path = link_from_mail()
        client.post(path)

        used = client.get(path)
        never_real = client.get("/accounts/link/completely-made-up/")

        assert used.status_code == never_real.status_code == 410
        assert "no longer works" in never_real.content.decode()

    def test_a_relative_destination_is_followed(self, client, speaker):
        services.send_login_link("speaker@example.org", next_url="/accounts/signed-in/", base_url="http://testserver")

        response = client.post(link_from_mail())

        assert response["Location"] == "/accounts/signed-in/"

    def test_an_offsite_destination_is_dropped(self, client, speaker):
        """A signed link that redirects off-site is a more convincing phish than an unsigned one."""
        services.send_login_link(
            "speaker@example.org", next_url="https://evil.example.com/", base_url="http://testserver"
        )

        response = client.post(link_from_mail())

        assert response["Location"] == "/accounts/signed-in/"

    def test_a_link_with_no_destination_lands_on_the_review_list(self, client, speaker):
        client.post(LOGIN, {"email": "speaker@example.org"})

        response = client.post(link_from_mail(), follow=True)

        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == "/review/"
