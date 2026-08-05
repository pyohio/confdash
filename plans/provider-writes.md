# Provider writes

Reads and writes to a provider fail differently, so they are handled differently.

A failed **read** costs a wait. Nothing local changed, the next sync picks up where this one left off,
and the upsert-and-never-delete rules mean a partial read cannot corrupt anything.

A failed **write** leaves the database asserting something about the provider that is not true. We
believe captions were corrected and YouTube serves the old ones; we believe a video is public and it
is unlisted. Nothing detects that, because the only record of the intent was the request that failed.

The YouTube quota makes this likely rather than hypothetical: one event's caption pass is most of a
day's allocation, `403 quotaExceeded` then blocks every call until midnight Pacific, and caption
writes cost 450 units each. Hitting the wall mid-batch is the expected case, not the edge case.

## The rule

**Local state records what the provider has confirmed. Intent lives in a durable queue.**

So nothing ever optimistically writes "published" and hopes. A publish sets the desired state in the
queue, the write executes, the provider confirms, and only then does `publication_state` become
`published`. Divergence stops being something to detect and reconcile, because there is no code path
that can produce it.

This is the transactional outbox pattern, and its load-bearing property is that the intent is
committed **in the same transaction as the local change**. There is no window where a speaker's
caption edit is saved but the intent to push it is lost.

Reads stay synchronous and direct. Sync services keep calling adapters straight, with no queue
involved, because there is nothing to protect.

## Shape

```
ProviderWrite
  event            FK -> Event
  capability                         # video_host today
  operation                          # set_privacy | upload_captions | update_metadata
  target_external_id                 # the provider's id for the thing being written
  desired          JSONField         # the intended end state, not a diff
  state                              # pending | in_flight | confirmed | failed | superseded
  attempts         int
  last_error       blank
  not_before       nullable          # quota deferral or a scheduled release
  confirmed_at     nullable
  requested_by     FK -> accounts.User, nullable
  unique (event, target_external_id, operation) where state is pending
```

Decisions embedded in that shape:

- **`desired` holds the end state, not a delta.** Retrying is then naturally idempotent, and a
  superseded request can be discarded rather than replayed in order.
- **One pending write per target and operation.** A speaker editing captions three times needs the
  latest content pushed once, not three uploads at 450 units each. A new request supersedes the
  pending one instead of queueing behind it.
- **`not_before` rather than pure backoff.** On `quotaExceeded` the right wait is not exponential, it
  is "after the next midnight Pacific", because that is when the quota actually returns. Generic retry
  policies get this wrong and burn units discovering the wall repeatedly. Every request costs at least
  one unit even when rejected.
- **Attempts are capped**, and an exhausted request becomes `failed` and visible rather than retrying
  forever.

## Confirmation

Whether to verify a write by reading it back depends on what the read costs.

- **Privacy changes: confirm by re-reading.** `videos.list` is 1 unit, and publication is the write
  whose divergence matters most. Cheap insurance.
- **Caption uploads: trust the returned track id.** Verifying means `captions.list` plus
  `captions.download` at 250 units, which is more than the write itself. The returned id plus a stored
  `content_hash` is enough.

## Guards run at execution, not enqueue

`do_not_record` and review state must be checked when the write executes. A talk can be marked
do-not-record, or an approval withdrawn, between enqueue and execution, and a queue that trusts its
own stale snapshot would publish something it should not. A guard that fails at execution moves the
request to `failed` with the reason, and never to `confirmed`.

## The table is the queue

No broker, no worker process, no job runner. At this volume, a few dozen writes per event a few times
a year, `ProviderWrite` rows plus `just manage drain_provider_writes` is the whole mechanism. Cron
calls the command if anything ever needs to run unattended, such as a write waiting on tomorrow's
quota.

The one detail that makes a table-as-queue correct rather than merely convenient:

```python
ProviderWrite.objects.select_for_update(skip_locked=True).filter(state="pending", not_before__lte=now)
```

`skip_locked` means two drains running at once take disjoint rows instead of blocking or
double-writing, which is the only real hazard of this approach and is a Postgres feature rather than
something to build. Claim rows inside a transaction, mark them `in_flight`, and a crashed drain leaves
them recoverable rather than lost.

What this deliberately avoids: **the requirement here is durable intent, not asynchronous execution.**
Those get conflated, and the result is infrastructure nobody needed. Intent is a data-model concern and
a table solves it completely. If throughput ever justifies a runner, django-tasks is the choice on
record, but nothing in M1 approaches that and the table does not have to change to get there.

Worth doing as part of M1.5 rather than after it, because retrofitting "record intent before acting"
means revisiting every call site that already acted.
