"""The provider-write queue: enqueue intent, claim it, execute it, record what was confirmed.

The table is the queue. No broker, no worker process, no job runner. At a few dozen writes per event a
few times a year, `ProviderWrite` rows plus `just manage drain_provider_writes` is the whole mechanism,
and the requirement is durable *intent*, not asynchronous execution. Those get conflated and the result
is infrastructure nobody needed.

This module owns the queue and knows nothing about what is being written. What a `set_privacy` write
means, whether it is still allowed, and what local state it settles are all decided by a handler the
owning app registers. So `integrations` never imports `videos`, and the layering matches the rest of
the provider abstraction: capabilities down here, domain knowledge above.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from integrations.models import ProviderWrite
from integrations.providers.base import Capability, QuotaExceeded, WriteRejected
from integrations.resolver import IntegrationNotConfigured, resolve_adapter

logger = structlog.get_logger(__name__)

# How long to wait after a transient failure. Flat rather than exponential: the failures worth retrying
# here are brief provider blips, and the one failure that genuinely needs a long wait (quota) says so
# explicitly via `QuotaExceeded.retry_after`.
RETRY_DELAY = timedelta(minutes=10)

# Fallback when a provider reports exhausted quota without saying when it returns.
UNKNOWN_QUOTA_DELAY = timedelta(hours=6)

# How long an `in_flight` write may sit untouched before it is presumed abandoned. Generous, because a
# drain is serial and each write is a couple of HTTP calls: anything still in flight this long is a
# crashed process, not a slow one.
STALE_IN_FLIGHT = timedelta(minutes=30)


class HandlerNotRegistered(Exception):
    """No app claimed this capability and operation, so nothing can execute it."""


# --- Handlers ---------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class WriteOutcome:
    """What a handler got back from the provider.

    Only `result` is recorded, because by the time a handler returns it has already moved local state
    to match: there is nothing left for the queue to apply.
    """

    result: dict = field(default_factory=dict)


_HANDLERS: dict[tuple[str, str], Callable] = {}


def handles(capability: Capability, operation: str):
    """Register the callable that executes one capability's operation.

    The handler is given `(write, adapter)` and is responsible for three things, in this order: check
    that the write is still allowed, perform it, and move local state to match what the provider
    confirmed. It returns a `WriteOutcome`, or raises `WriteRejected` to refuse permanently.

    Guards belong inside the handler rather than at enqueue time. A talk can be marked do-not-record,
    or an approval withdrawn, between enqueue and execution, and a queue that trusted its own stale
    snapshot would publish something it should not.
    """

    def decorate(func):
        key = (str(capability), str(operation))
        if key in _HANDLERS and _HANDLERS[key] is not func:
            raise ValueError(f"A handler for {key[0]}/{key[1]} is already registered.")
        _HANDLERS[key] = func
        return func

    return decorate


def get_handler(capability: str, operation: str) -> Callable:
    try:
        return _HANDLERS[(str(capability), str(operation))]
    except KeyError as exc:
        raise HandlerNotRegistered(
            f"No handler is registered for {capability}/{operation}. "
            "The app that owns this operation must register one at app-ready time."
        ) from exc


# --- Enqueue ----------------------------------------------------------------


def enqueue(
    *,
    event,
    capability: Capability | str,
    operation: str,
    target_external_id: str,
    desired: dict,
    requested_by=None,
    not_before: datetime | None = None,
) -> ProviderWrite:
    """Record the intent to write, superseding any pending write for the same target and operation.

    Call this inside the transaction that makes the local change it follows from, which is the whole
    point: intent and change commit together or neither does.

    Raises `HandlerNotRegistered` rather than accepting a row nothing can execute. A write that can
    only ever fail at drain time is worse than a loud failure at the call site.
    """
    capability = Capability(capability)
    get_handler(capability, operation)

    for attempt in (1, 2):
        try:
            with transaction.atomic():
                pending = ProviderWrite.objects.filter(
                    event=event,
                    capability=capability,
                    operation=operation,
                    target_external_id=target_external_id,
                    state=ProviderWrite.State.PENDING,
                )
                existing = pending.select_for_update().first()

                if existing is not None and existing.desired == desired:
                    # Re-enqueueing the same end state is a no-op, not a reason to churn the row and
                    # lose its place in the queue.
                    return existing

                superseded = pending.update(state=ProviderWrite.State.SUPERSEDED, updated_at=timezone.now())
                write = ProviderWrite.objects.create(
                    event=event,
                    capability=capability,
                    operation=operation,
                    target_external_id=target_external_id,
                    desired=desired,
                    requested_by=requested_by,
                    not_before=not_before,
                )
        except IntegrityError:
            # Two callers raced on the partial unique index. The loser retries once and finds the
            # winner's row, which is the correct outcome: one pending write, latest intent.
            if attempt == 2:
                raise
            continue

        logger.info(
            "provider_write.enqueued",
            event_slug=event.slug,
            capability=str(capability),
            operation=operation,
            target_external_id=target_external_id,
            superseded=superseded,
        )
        return write

    raise AssertionError("unreachable")  # pragma: no cover


# --- Claiming ---------------------------------------------------------------


def requeue_stale(*, event=None, now: datetime | None = None, older_than: timedelta = STALE_IN_FLIGHT) -> int:
    """Return abandoned in-flight writes to the queue.

    Marking a row `in_flight` before attempting it is what makes a crash recoverable rather than
    invisible, but only if something actually recovers it. Without this a drain killed mid-write leaves a
    caption edit or a publication that silently never happens, and `in_flight` becomes a state nothing
    ever leaves.

    Safe to be wrong about: `desired` is an end state, so re-running a write that did in fact succeed
    asks the provider for something already true. The cost of a false positive is quota, not correctness,
    which is why the threshold can afford to be generous.

    Counts as an attempt, so a write that reliably kills the process fails visibly instead of looping.
    """
    now = now or timezone.now()
    stale = ProviderWrite.objects.filter(state=ProviderWrite.State.IN_FLIGHT, updated_at__lte=now - older_than)
    if event is not None:
        stale = stale.filter(event=event)

    count = 0
    for write in stale:
        mark_failed(write, error="Presumed abandoned in flight; requeued.", now=now)
        count += 1

    if count:
        logger.warning("provider_write.requeued_stale", count=count)
    return count


def claim(*, event=None, limit: int = 25, now: datetime | None = None) -> list[ProviderWrite]:
    """Take up to `limit` due writes and mark them in flight.

    `select_for_update(skip_locked=True)` is the one detail that makes a table-as-queue correct rather
    than merely convenient: two drains running at once take disjoint rows instead of blocking or
    double-writing. Claiming inside a transaction and marking `in_flight` means a crashed drain leaves
    rows recoverable rather than lost.
    """
    now = now or timezone.now()

    with transaction.atomic():
        due = (
            ProviderWrite.objects.select_for_update(skip_locked=True)
            .filter(state=ProviderWrite.State.PENDING)
            .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
        )
        if event is not None:
            due = due.filter(event=event)

        claimed = list(due.order_by("created_at")[:limit])
        if claimed:
            # A bulk update rather than per-row saves, so the whole claim is one statement. It bypasses
            # `auto_now`, hence the explicit timestamp.
            ProviderWrite.objects.filter(pk__in=[w.pk for w in claimed]).update(
                state=ProviderWrite.State.IN_FLIGHT, updated_at=now
            )
            for write in claimed:
                write.state = ProviderWrite.State.IN_FLIGHT

    return claimed


# --- Settling ---------------------------------------------------------------


def mark_confirmed(write: ProviderWrite, *, result: dict | None = None, now: datetime | None = None) -> ProviderWrite:
    now = now or timezone.now()
    write.state = ProviderWrite.State.CONFIRMED
    write.result = result or {}
    write.confirmed_at = now
    write.last_error = ""
    write.save(update_fields=["state", "result", "confirmed_at", "last_error", "updated_at"])
    return write


def mark_failed(write: ProviderWrite, *, error: str, permanent: bool = False, now: datetime | None = None):
    """Settle a failed attempt, either as retryable or as done trying.

    A permanent rejection stops immediately: retrying a caption track the provider called malformed
    only spends allowance. Everything else retries until `MAX_ATTEMPTS`, then becomes visibly failed
    rather than churning forever.
    """
    now = now or timezone.now()
    write.attempts += 1
    write.last_error = error

    if permanent or not write.attempts_remain:
        write.state = ProviderWrite.State.FAILED
        write.not_before = None
    else:
        write.state = ProviderWrite.State.PENDING
        write.not_before = now + RETRY_DELAY

    write.save(update_fields=["state", "attempts", "last_error", "not_before", "updated_at"])
    return write


def defer(write: ProviderWrite, *, until: datetime, error: str = "") -> ProviderWrite:
    """Push a write out to a known-good time without spending an attempt.

    Exhausted quota is not the write failing. Counting it as an attempt would retire a perfectly good
    request for the sin of being third in a queue that ran out of allowance.
    """
    write.state = ProviderWrite.State.PENDING
    write.not_before = until
    write.last_error = error
    write.save(update_fields=["state", "not_before", "last_error", "updated_at"])
    return write


# --- Draining ---------------------------------------------------------------


@dataclass
class DrainResult:
    confirmed: int = 0
    failed: int = 0
    deferred: int = 0
    # Writes a previous run abandoned in flight, returned to the queue rather than attempted here.
    requeued: int = 0

    @property
    def attempted(self) -> int:
        return self.confirmed + self.failed + self.deferred


def drain(*, event=None, limit: int = 25, now: datetime | None = None) -> DrainResult:
    """Execute every due write, one at a time, settling each before moving on.

    Serial on purpose. These writes are expensive in provider allowance and few in number, and stopping
    after the first `QuotaExceeded` matters more than throughput: every further call costs a unit even
    when rejected, so a batch that has hit the wall defers the rest rather than proving it repeatedly.

    Recovery runs first, so an abandoned write is picked up by whatever runs next rather than needing its
    own command. It is not attempted in this run: something killed the last process holding it, and a
    short wait before trying again is cheap.
    """
    result = DrainResult()
    result.requeued = requeue_stale(event=event, now=now)
    adapters: dict[tuple, object] = {}
    blocked_until: dict[tuple, datetime] = {}

    for write in claim(event=event, limit=limit, now=now):
        key = (write.event_id, write.capability)

        if key in blocked_until:
            defer(write, until=blocked_until[key], error="Deferred: provider quota already exhausted this drain.")
            result.deferred += 1
            continue

        try:
            if key not in adapters:
                adapters[key] = resolve_adapter(write.event, write.capability)
            adapter = adapters[key]
            handler = get_handler(write.capability, write.operation)
            outcome = handler(write, adapter)
        except QuotaExceeded as exc:
            until = exc.retry_after or (timezone.now() + UNKNOWN_QUOTA_DELAY)
            blocked_until[key] = until
            defer(write, until=until, error=f"QuotaExceeded: {exc}")
            result.deferred += 1
            logger.warning(
                "provider_write.deferred",
                event_slug=write.event.slug,
                operation=write.operation,
                target_external_id=write.target_external_id,
                retry_after=until.isoformat(),
            )
            continue
        except (WriteRejected, IntegrationNotConfigured, HandlerNotRegistered) as exc:
            mark_failed(write, error=f"{type(exc).__name__}: {exc}", permanent=True)
            result.failed += 1
            logger.warning(
                "provider_write.rejected",
                event_slug=write.event.slug,
                operation=write.operation,
                target_external_id=write.target_external_id,
                error=str(exc),
            )
            continue
        except Exception as exc:
            mark_failed(write, error=f"{type(exc).__name__}: {exc}")
            result.failed += 1
            logger.warning(
                "provider_write.failed",
                event_slug=write.event.slug,
                operation=write.operation,
                target_external_id=write.target_external_id,
                attempts=write.attempts,
                error=str(exc),
            )
            continue

        mark_confirmed(write, result=outcome.result if outcome else {})
        result.confirmed += 1
        logger.info(
            "provider_write.confirmed",
            event_slug=write.event.slug,
            operation=write.operation,
            target_external_id=write.target_external_id,
        )

    return result
