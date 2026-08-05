"""Video-host writes: what gets queued, and what executing one is allowed to do.

The queue's own mechanics are tested in `integrations/tests/test_outbox.py`. What matters here is the
rule those mechanics exist to serve: **no local state moves until the provider confirms**, and the
guards are re-checked at execution rather than trusted from enqueue time.
"""

import pytest
from django.utils import timezone

from integrations.models import ProviderWrite
from integrations.outbox import drain
from integrations.providers.base import PrivacyStatus, VideoRecord, WriteRejected
from program.models import Talk
from videos import writes
from videos.models import Video

pytestmark = pytest.mark.integration


@pytest.fixture
def talk(event):
    return Talk.objects.create(event=event, external_id="TALK1", title="A Talk", state="confirmed")


@pytest.fixture
def approved_video(event, talk, user):
    """A video that has been matched and approved, so publication is permitted."""
    return Video.objects.create(
        event=event,
        talk=talk,
        external_id="vid123",
        title="A Talk",
        privacy_status=Video.PrivacyStatus.UNLISTED,
        review_state=Video.ReviewState.APPROVED,
        approval_source=Video.ApprovalSource.SPEAKER,
        approved_by=user,
        approved_at=timezone.now(),
    )


@pytest.fixture
def on_provider(fake_videos):
    """Put the video on the fake host so a write has something to act on."""

    def install(external_id="vid123", privacy=PrivacyStatus.UNLISTED, **kwargs):
        fake_videos(videos=[VideoRecord(external_id=external_id, title="A Talk", privacy_status=privacy)], **kwargs)

    return install


class TestRequesting:
    def test_queueing_publication_does_not_publish_anything(self, approved_video):
        """The point of the whole design: a click records intent, it does not assert an outcome."""
        writes.request_publication(approved_video, user=approved_video.approved_by)

        approved_video.refresh_from_db()
        assert approved_video.publication_state == Video.PublicationState.UNPUBLISHED
        assert approved_video.privacy_status == Video.PrivacyStatus.UNLISTED

        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.PENDING
        assert write.desired == {"privacy_status": "public"}
        assert write.target_external_id == "vid123"

    def test_refuses_to_queue_an_ineligible_video(self, event, talk):
        unapproved = Video.objects.create(event=event, talk=talk, external_id="vid999", title="A Talk")

        with pytest.raises(ValueError, match="not eligible"):
            writes.request_publication(unapproved)

        assert not ProviderWrite.objects.exists()

    def test_records_who_asked(self, approved_video, user):
        write = writes.request_publication(approved_video, user=user)

        assert write.requested_by == user

    def test_caption_upload_carries_the_content_it_was_given(self, approved_video):
        """Content rides in `desired` so what gets uploaded is what the reviewer approved."""
        write = writes.request_caption_upload(approved_video, language="en", content="1\n00:00 hello\n")

        assert write.desired["language"] == "en"
        assert write.desired["content"] == "1\n00:00 hello\n"
        assert write.desired["content_hash"] == writes.content_hash("1\n00:00 hello\n")

    def test_refuses_an_empty_caption_track(self, approved_video):
        with pytest.raises(ValueError, match="empty caption track"):
            writes.request_caption_upload(approved_video, language="en", content="   ")

    def test_a_retraction_needs_no_eligibility(self, event, talk):
        """Making something less visible must never be gated by the rules that gate publishing."""
        video = Video.objects.create(event=event, talk=talk, external_id="vid999", title="A Talk")

        write = writes.request_privacy(video, PrivacyStatus.PRIVATE)

        assert write.desired == {"privacy_status": "private"}


class TestPublishing:
    def test_confirmation_is_what_moves_local_state(self, approved_video, video_binding, on_provider):
        on_provider()
        writes.request_publication(approved_video)

        result = drain()

        approved_video.refresh_from_db()
        assert result.confirmed == 1
        assert approved_video.publication_state == Video.PublicationState.PUBLISHED
        assert approved_video.privacy_status == Video.PrivacyStatus.PUBLIC

    def test_confirmation_reads_the_video_back(self, approved_video, video_binding, on_provider):
        """A privacy change is confirmed by re-reading, because believing a video is public when it is
        unlisted means telling a speaker their talk is out while nobody can find it."""
        on_provider()
        writes.request_publication(approved_video)

        drain()

        assert ProviderWrite.objects.get().result["confirmed_by"] == "read_back"

    def test_a_write_the_provider_has_not_applied_yet_stays_pending(self, approved_video, video_binding, on_provider):
        """Eventual consistency is a normal outcome, not a failure: retry rather than give up."""
        on_provider(ignore_writes=True)
        writes.request_publication(approved_video)

        drain()

        approved_video.refresh_from_db()
        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.PENDING
        assert write.attempts == 1
        # The important half: nothing local moved on the strength of an unconfirmed write.
        assert approved_video.publication_state == Video.PublicationState.UNPUBLISHED

    def test_a_retraction_returns_the_video_to_unpublished(self, approved_video, video_binding, on_provider):
        on_provider(privacy=PrivacyStatus.PUBLIC)
        approved_video.publication_state = Video.PublicationState.PUBLISHED
        approved_video.privacy_status = Video.PrivacyStatus.PUBLIC
        approved_video.save()

        writes.request_privacy(approved_video, PrivacyStatus.UNLISTED)
        drain()

        approved_video.refresh_from_db()
        assert approved_video.publication_state == Video.PublicationState.UNPUBLISHED
        assert approved_video.privacy_status == Video.PrivacyStatus.UNLISTED


class TestGuardsRunAtExecution:
    """The reason guards are not evaluated at enqueue time.

    Each of these queues a legitimate write and then changes the world underneath it. A queue trusting
    its own snapshot would publish something it should not.
    """

    def test_a_withdrawn_approval_stops_the_write(self, approved_video, video_binding, on_provider):
        on_provider()
        writes.request_publication(approved_video)

        approved_video.review_state = Video.ReviewState.CHANGES_REQUESTED
        approved_video.save(update_fields=["review_state"])

        result = drain()

        approved_video.refresh_from_db()
        assert result.failed == 1
        assert ProviderWrite.objects.get().state == ProviderWrite.State.FAILED
        assert approved_video.privacy_status == Video.PrivacyStatus.UNLISTED
        assert approved_video.publication_state == Video.PublicationState.UNPUBLISHED

    def test_do_not_record_stops_the_write(self, approved_video, talk, video_binding, on_provider):
        on_provider()
        writes.request_publication(approved_video)

        talk.do_not_record = True
        talk.save(update_fields=["do_not_record"])

        drain()

        approved_video.refresh_from_db()
        assert ProviderWrite.objects.get().state == ProviderWrite.State.FAILED
        assert approved_video.publication_state == Video.PublicationState.UNPUBLISHED

    def test_do_not_record_also_stops_a_caption_upload(self, approved_video, talk, video_binding, on_provider):
        """Not just publication. A do-not-record talk's video is not written to at all."""
        on_provider()
        writes.request_caption_upload(approved_video, language="en", content="captions")

        talk.do_not_record = True
        talk.save(update_fields=["do_not_record"])

        drain()

        assert ProviderWrite.objects.get().state == ProviderWrite.State.FAILED

    def test_a_retraction_is_never_blocked_by_a_guard(self, approved_video, video_binding, on_provider):
        """A video pulled back must go back whatever its review state now says."""
        on_provider(privacy=PrivacyStatus.PUBLIC)
        writes.request_privacy(approved_video, PrivacyStatus.PRIVATE)

        approved_video.review_state = Video.ReviewState.CHANGES_REQUESTED
        approved_video.save(update_fields=["review_state"])

        result = drain()

        approved_video.refresh_from_db()
        assert result.confirmed == 1
        assert approved_video.privacy_status == Video.PrivacyStatus.PRIVATE

    def test_a_video_that_left_the_playlist_fails_permanently(self, approved_video, video_binding, on_provider):
        on_provider()
        writes.request_publication(approved_video)
        approved_video.delete()

        drain()

        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.FAILED
        assert "may have left the playlist" in write.last_error


class TestCaptionUpload:
    def test_trusts_the_returned_track_id(self, approved_video, video_binding, on_provider):
        """Verifying costs `captions.list` plus `captions.download` at 250 units, more than the write."""
        on_provider()
        writes.request_caption_upload(approved_video, language="en", content="captions here")

        result = drain()

        write = ProviderWrite.objects.get()
        assert result.confirmed == 1
        assert write.result["track_id"] == "fake-caption-vid123-en"
        assert write.result["content_hash"] == writes.content_hash("captions here")

    def test_uploads_exactly_the_content_queued(self, approved_video, video_binding, on_provider):
        """Not the current content: what the reviewer approved, even if the source changed since."""
        from integrations.tests.fakes import FakeVideoHost

        on_provider()
        writes.request_caption_upload(approved_video, language="en", content="the approved text")

        drain()

        assert [(external_id, track.content) for external_id, track in FakeVideoHost.uploaded_captions] == [
            ("vid123", "the approved text")
        ]

    def test_a_provider_rejection_does_not_retry(self, approved_video, video_binding, on_provider):
        on_provider(upload_captions_error=WriteRejected("caption track is malformed"))
        writes.request_caption_upload(approved_video, language="en", content="captions")

        drain()

        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.FAILED
        assert write.attempts == 1


class TestStandaloneVideos:
    def test_a_staff_approved_standalone_video_publishes(self, event, user, video_binding, on_provider):
        """Nothing standalone is stranded unpublishable: it reaches the same path with no talk to guard."""
        video = Video.objects.create(
            event=event,
            external_id="vid123",
            title="Closing remarks",
            standalone=True,
            review_state=Video.ReviewState.APPROVED,
            approval_source=Video.ApprovalSource.STAFF,
            approved_by=user,
            approved_at=timezone.now(),
        )
        on_provider()

        writes.request_publication(video, user=user)
        result = drain()

        video.refresh_from_db()
        assert result.confirmed == 1
        assert video.publication_state == Video.PublicationState.PUBLISHED

    def test_an_unmatched_video_cannot_be_queued(self, event, on_provider):
        """Awaiting a matching decision means there is no answer to who approved it or for whom."""
        video = Video.objects.create(
            event=event, external_id="vid123", title="Something", review_state=Video.ReviewState.APPROVED
        )

        with pytest.raises(ValueError, match="not eligible"):
            writes.request_publication(video)
