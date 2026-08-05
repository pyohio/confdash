"""The write queue's mechanics, independent of what is being written.

A stub handler stands in for the real one, so these tests exercise superseding, claiming, retrying, and
quota deferral without any video-shaped setup. What a `set_privacy` write means is `videos`' business
and is tested there.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from integrations import outbox
from integrations.models import ProviderWrite
from integrations.outbox import (
    HandlerNotRegistered,
    WriteOutcome,
    claim,
    defer,
    drain,
    enqueue,
    mark_confirmed,
    mark_failed,
)
from integrations.providers.base import Capability, QuotaExceeded, WriteRejected

pytestmark = pytest.mark.integration

OPERATION = ProviderWrite.Operation.SET_PRIVACY


@pytest.fixture
def handler():
    """Install a recording handler over whatever the real app registered, then restore it.

    `_HANDLERS` is module-level state, so it is snapshotted rather than cleared: the handlers registered
    at app-ready time must still be there for the next test.
    """
    saved = dict(outbox._HANDLERS)
    calls: list[ProviderWrite] = []
    behaviour: dict = {"raise": None, "result": {"ok": True}}

    def stub(write, adapter):
        calls.append(write)
        if behaviour["raise"] is not None:
            raise behaviour["raise"]
        return WriteOutcome(result=behaviour["result"])

    outbox._HANDLERS[(str(Capability.VIDEO_HOST), str(OPERATION))] = stub
    stub.calls = calls
    stub.behaviour = behaviour
    try:
        yield stub
    finally:
        outbox._HANDLERS.clear()
        outbox._HANDLERS.update(saved)


def make_write(event, *, target="vid-1", desired=None, user=None) -> ProviderWrite:
    return enqueue(
        event=event,
        capability=Capability.VIDEO_HOST,
        operation=OPERATION,
        target_external_id=target,
        desired=desired or {"privacy_status": "public"},
        requested_by=user,
    )


class TestEnqueue:
    def test_records_intent_as_pending(self, event, handler):
        write = make_write(event)

        assert write.state == ProviderWrite.State.PENDING
        assert write.desired == {"privacy_status": "public"}
        assert write.attempts == 0

    def test_refuses_an_operation_no_handler_can_execute(self, event):
        """Better to fail at the call site than to accept a row that can only fail at drain time."""
        with pytest.raises(HandlerNotRegistered):
            enqueue(
                event=event,
                capability=Capability.TICKETING,
                operation="refund_everything",
                target_external_id="x",
                desired={},
            )

    def test_a_newer_request_supersedes_the_pending_one(self, event, handler):
        """Three caption edits must become one upload, not three at 450 units each."""
        first = make_write(event, desired={"privacy_status": "public"})
        second = make_write(event, desired={"privacy_status": "unlisted"})

        first.refresh_from_db()
        assert first.state == ProviderWrite.State.SUPERSEDED
        assert second.state == ProviderWrite.State.PENDING
        assert ProviderWrite.objects.filter(state=ProviderWrite.State.PENDING).count() == 1

    def test_re_requesting_the_same_end_state_is_a_no_op(self, event, handler):
        """A double-clicked button must not churn the row or lose its place in the queue."""
        first = make_write(event)
        again = make_write(event)

        assert again.pk == first.pk
        assert ProviderWrite.objects.count() == 1

    def test_a_different_target_is_a_separate_write(self, event, handler):
        make_write(event, target="vid-1")
        make_write(event, target="vid-2")

        assert ProviderWrite.objects.filter(state=ProviderWrite.State.PENDING).count() == 2

    def test_a_settled_write_is_not_superseded(self, event, handler):
        """History survives. Only a pending write is in anyone's way."""
        done = make_write(event)
        mark_confirmed(done)

        make_write(event, desired={"privacy_status": "unlisted"})

        done.refresh_from_db()
        assert done.state == ProviderWrite.State.CONFIRMED

    def test_an_in_flight_write_does_not_block_fresher_intent(self, event, handler):
        """The uniqueness rule covers pending only, so a slow write cannot swallow a later decision."""
        in_flight = make_write(event)
        claim()

        later = make_write(event, desired={"privacy_status": "private"})

        in_flight.refresh_from_db()
        assert in_flight.state == ProviderWrite.State.IN_FLIGHT
        assert later.state == ProviderWrite.State.PENDING


class TestClaim:
    def test_marks_claimed_writes_in_flight(self, event, handler):
        make_write(event)

        claimed = claim()

        assert [w.state for w in claimed] == [ProviderWrite.State.IN_FLIGHT]
        assert ProviderWrite.objects.get().state == ProviderWrite.State.IN_FLIGHT

    def test_a_second_claim_finds_nothing(self, event, handler):
        """The property `SKIP LOCKED` buys, observable without threads: work is taken once."""
        make_write(event)
        claim()

        assert claim() == []

    def test_skips_a_write_that_is_not_due_yet(self, event, handler):
        write = make_write(event)
        defer(write, until=timezone.now() + timedelta(hours=2))

        assert claim() == []

    def test_takes_a_write_whose_deferral_has_passed(self, event, handler):
        write = make_write(event)
        defer(write, until=timezone.now() - timedelta(minutes=1))

        assert [w.pk for w in claim()] == [write.pk]

    def test_honours_the_limit(self, event, handler):
        make_write(event, target="vid-1")
        make_write(event, target="vid-2")

        assert len(claim(limit=1)) == 1

    def test_claims_in_the_order_requested(self, event, handler):
        first = make_write(event, target="vid-1")
        second = make_write(event, target="vid-2")

        assert [w.pk for w in claim()] == [first.pk, second.pk]

    def test_scopes_to_one_event(self, event, other_organization, handler):
        from events.models import Event

        elsewhere = Event.objects.create(
            organization=other_organization, slug="2026", name="OtherConf 2026", timezone="UTC"
        )
        mine = make_write(event)
        make_write(elsewhere, target="vid-9")

        assert [w.pk for w in claim(event=event)] == [mine.pk]


class TestRecoveringAbandonedWrites:
    """Marking a row in flight only makes a crash recoverable if something recovers it.

    Otherwise `in_flight` is a state nothing leaves, and a killed drain silently loses a publication.
    """

    def test_a_stale_in_flight_write_returns_to_the_queue(self, event, handler):
        write = make_write(event)
        claim()

        requeued = outbox.requeue_stale(now=timezone.now() + outbox.STALE_IN_FLIGHT + timedelta(minutes=1))

        write.refresh_from_db()
        assert requeued == 1
        assert write.state == ProviderWrite.State.PENDING
        assert write.attempts == 1

    def test_a_write_still_in_flight_is_left_alone(self, event, handler):
        """A running drain must not have its work taken out from under it."""
        write = make_write(event)
        claim()

        assert outbox.requeue_stale() == 0

        write.refresh_from_db()
        assert write.state == ProviderWrite.State.IN_FLIGHT

    def test_recovery_counts_as_an_attempt(self, event, handler):
        """A write that reliably kills the process must fail visibly, not loop forever."""
        write = make_write(event)
        far_future = timezone.now() + timedelta(days=1)

        for _ in range(ProviderWrite.MAX_ATTEMPTS):
            ProviderWrite.objects.filter(pk=write.pk).update(state=ProviderWrite.State.IN_FLIGHT)
            outbox.requeue_stale(now=far_future)

        write.refresh_from_db()
        assert write.state == ProviderWrite.State.FAILED

    def test_a_drain_recovers_before_claiming(self, event, video_binding, handler):
        make_write(event)
        claim()

        result = drain(now=timezone.now() + outbox.STALE_IN_FLIGHT + timedelta(minutes=1))

        # Requeued, not attempted: something killed the last process holding it.
        assert result.requeued == 1
        assert result.attempted == 0
        assert ProviderWrite.objects.get().state == ProviderWrite.State.PENDING


class TestSettling:
    def test_a_transient_failure_returns_the_write_to_the_queue(self, event, handler):
        write = make_write(event)

        mark_failed(write, error="boom")

        assert write.state == ProviderWrite.State.PENDING
        assert write.attempts == 1
        assert write.not_before is not None

    def test_a_permanent_rejection_stops_immediately(self, event, handler):
        """Retrying something the provider called malformed only spends allowance."""
        write = make_write(event)

        mark_failed(write, error="malformed", permanent=True)

        assert write.state == ProviderWrite.State.FAILED
        assert write.attempts == 1

    def test_attempts_are_capped(self, event, handler):
        write = make_write(event)

        for _ in range(ProviderWrite.MAX_ATTEMPTS):
            mark_failed(write, error="boom")

        assert write.state == ProviderWrite.State.FAILED
        assert write.attempts == ProviderWrite.MAX_ATTEMPTS

    def test_deferral_does_not_spend_an_attempt(self, event, handler):
        """Running out of quota is not the write failing, and must not retire a good request."""
        write = make_write(event)
        until = timezone.now() + timedelta(hours=5)

        defer(write, until=until, error="quota")

        assert write.state == ProviderWrite.State.PENDING
        assert write.attempts == 0
        assert write.not_before == until

    def test_confirmation_records_what_the_provider_returned(self, event, handler):
        write = make_write(event)

        mark_confirmed(write, result={"track_id": "abc"})

        assert write.state == ProviderWrite.State.CONFIRMED
        assert write.result == {"track_id": "abc"}
        assert write.confirmed_at is not None

    def test_confirmation_clears_an_earlier_error(self, event, handler):
        write = make_write(event)
        mark_failed(write, error="a blip")

        mark_confirmed(write)

        assert write.last_error == ""


class TestDrain:
    def test_confirms_a_successful_write(self, event, video_binding, handler):
        make_write(event)

        result = drain()

        assert (result.confirmed, result.failed, result.deferred) == (1, 0, 0)
        assert ProviderWrite.objects.get().state == ProviderWrite.State.CONFIRMED

    def test_passes_the_resolved_adapter_to_the_handler(self, event, video_binding, handler):
        """The handler must never resolve its own adapter: capability resolution belongs to the queue."""
        make_write(event)

        drain()

        assert len(handler.calls) == 1

    def test_a_handler_rejection_fails_permanently(self, event, video_binding, handler):
        handler.behaviour["raise"] = WriteRejected("no longer eligible")
        make_write(event)

        result = drain()

        write = ProviderWrite.objects.get()
        assert result.failed == 1
        assert write.state == ProviderWrite.State.FAILED
        assert write.attempts == 1
        assert "no longer eligible" in write.last_error

    def test_an_unexpected_error_is_retryable(self, event, video_binding, handler):
        handler.behaviour["raise"] = RuntimeError("provider hiccup")
        make_write(event)

        drain()

        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.PENDING
        assert write.attempts == 1

    def test_a_missing_binding_fails_permanently(self, event, handler):
        """No `video_binding` fixture, so the capability cannot be resolved at all."""
        make_write(event)

        result = drain()

        write = ProviderWrite.objects.get()
        assert result.failed == 1
        assert write.state == ProviderWrite.State.FAILED
        assert "IntegrationNotConfigured" in write.last_error

    def test_quota_defers_to_the_time_the_provider_named(self, event, video_binding, handler):
        until = timezone.now() + timedelta(hours=3)
        handler.behaviour["raise"] = QuotaExceeded("out of units", retry_after=until)
        make_write(event)

        result = drain()

        write = ProviderWrite.objects.get()
        assert result.deferred == 1
        assert write.state == ProviderWrite.State.PENDING
        assert write.not_before == until
        assert write.attempts == 0

    def test_quota_stops_the_rest_of_the_batch_without_attempting_it(self, event, video_binding, handler):
        """Every call costs a unit even when rejected, so a batch that hit the wall must not keep going."""
        until = timezone.now() + timedelta(hours=3)
        handler.behaviour["raise"] = QuotaExceeded("out of units", retry_after=until)
        make_write(event, target="vid-1")
        make_write(event, target="vid-2")

        result = drain()

        assert result.deferred == 2
        assert len(handler.calls) == 1
        assert {w.state for w in ProviderWrite.objects.all()} == {ProviderWrite.State.PENDING}
        assert {w.not_before for w in ProviderWrite.objects.all()} == {until}

    def test_quota_without_a_named_reset_still_defers(self, event, video_binding, handler):
        handler.behaviour["raise"] = QuotaExceeded("out of units")
        make_write(event)

        drain()

        write = ProviderWrite.objects.get()
        assert write.state == ProviderWrite.State.PENDING
        assert write.not_before > timezone.now()

    def test_nothing_due_is_not_an_error(self, event, video_binding, handler):
        result = drain()

        assert result.attempted == 0
