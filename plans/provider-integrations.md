# Provider integrations

The app depends on capabilities, not on Pretalx, Tito, or YouTube. Each capability is a
protocol; each provider is an adapter implementing it. Model shapes are in
[data-model.md](data-model.md).

PyOhio's current stack is one configuration of this system. Nothing outside
`integrations/providers/` may import a provider module or reference a provider by name.

## Capabilities

| Capability | Purpose | v1 provider |
| --- | --- | --- |
| `talk_source` | Talks, speakers, schedule | `pretalx` |
| `ticketing` | Registrations, orders, attendee questions | `tito` (M2) |
| `video_host` | Video listing, captions, privacy control | `youtube` |

## Layout

```
src/integrations/
  models.py                 ProviderConnection, EventProviderBinding
  registry.py               provider lookup by (capability, provider)
  credentials.py            encrypt / decrypt of the credentials payload
  resolver.py               event + capability -> configured adapter instance
  providers/
    base.py                 capability protocols and shared dataclasses
    pretalx.py              talk_source
    youtube.py              video_host
    tito.py                 ticketing (M2)
```

## Adapter contract

Adapters are thin. They talk to the remote API over httpx and return provider-neutral
dataclasses; they never touch the ORM. Sync services own all persistence, so a provider bug
cannot corrupt local state and an adapter is testable without a database.

```python
class TalkSource(Protocol):
    def fetch_speakers(self) -> Iterable[SpeakerRecord]: ...
    def fetch_talks(self) -> Iterable[TalkRecord]: ...


class VideoHost(Protocol):
    def list_videos(self) -> Iterable[VideoRecord]: ...
    def fetch_captions(self, external_id: str) -> Iterable[CaptionRecord]: ...
    def upload_captions(self, external_id: str, track: CaptionRecord) -> str: ...
    def set_privacy(self, external_id: str, status: PrivacyStatus) -> None: ...
```

`*Record` dataclasses carry a `raw` dict alongside normalized fields, which is what lands in
the models' `raw` JSONField.

Each adapter declares the config keys it needs at both levels, so the admin can validate a
binding instead of failing at first sync:

```python
@register
class PretalxTalkSource(BaseAdapter):
    capability = Capability.TALK_SOURCE
    provider = "pretalx"

    connection_config_keys = (ConfigKey(name="api_base_url", required=False),)
    credential_keys = (ConfigKey(name="api_token", required=True, secret=True),)
    event_config_keys = (ConfigKey(name="event_id", required=True),)
```

`ProviderConnection.clean()` and `EventProviderBinding.clean()` read these, so the admin reports
a missing `event_id` at save time instead of at first sync. `resolve_adapter` checks them again
before instantiating, since bindings can also be created in code and fixtures.

## Resolution

```python
adapter = resolve_adapter(event, capability="talk_source")
```

Resolution reads the `EventProviderBinding` for that event and capability, decrypts the
connection credentials, merges connection config under event config, and instantiates the
registered adapter class. It raises a typed error when no binding exists, when the binding's
connection is inactive, or when required config keys are missing — never a bare `KeyError` from
a provider module.

## Credentials

Encrypted at rest on `ProviderConnection.credentials_encrypted` with a Fernet key from
`FIELD_ENCRYPTION_KEY`, read and written only through `get_credentials()` / `set_credentials()`.
The payload is a provider-shaped dict, so Pretalx stores
`{"api_token": ...}` and YouTube stores `{"client_id": ..., "client_secret": ..., "refresh_token": ...}`
without needing a column per provider.

Rules that follow from holding tenant secrets:

- Never in `dumpdata` output. The field serializes to ciphertext, and fixtures used for
  development contain fake connections only.
- Never in logs. structlog gets a processor that redacts `credentials`, `api_token`,
  `refresh_token`, `client_secret`, and `Authorization`.
- Never in the admin as readable text. Write-only widget; the changelist shows
  `last_verified_at` and a masked hint, not the value.
- `FIELD_ENCRYPTION_KEY` hard-fails startup when `DEBUG=False`. Under `DEBUG=True` a fixed
  development key is allowed so a fresh checkout works.
- Key rotation needs a re-encrypt management command before it is a real operational option.
  Not built at bootstrap; noted in issues.

## Sync semantics

All syncs are idempotent upserts keyed on `(event, external_id)`. Never delete on absence: a
provider returning a short list because of a paging bug or a permissions change must not wipe
local rows and their review state. Records that disappear get flagged for organizer attention
instead.

Each sync writes a `SyncRun` row — capability, provider, counts, duration, success, error
detail — which is the operational surface for "did the Pretalx pull actually work." This is the
useful part of the legacy project's `SystemEvent` model, scoped down.

## Testing

- Adapters are tested against recorded JSON response fixtures, committed under
  `src/integrations/tests/fixtures/<provider>/`. Recording is manual and documented in the test module; no live
  network in the suite.
- Capability protocols get a shared conformance test that every adapter runs against, so a new
  provider fails loudly if it returns the wrong shape.
- Sync services are tested with a fake adapter, no HTTP involved, which is where the
  never-delete-on-absence and idempotency behavior gets covered.
- `resolve_adapter` is tested for the error paths: missing binding, inactive connection,
  cross-organization binding, missing required config.

## Reference: existing PyOhio integrations

Working code to port rather than reinvent.

- **Pretalx**: `../static-website/pyohio-cli/src/pyohio_cli/pretalx/`. httpx client with
  `Authorization: Token <key>` and `Pretalx-Version: v1` against
  `https://pretalx.com/api/events/<event_id>`. Cursor pagination via `next`. Useful endpoints:
  `/submissions/?state=confirmed&expand=speakers,submission_type`, `/speakers/<code>/`,
  `/slots/?expand=room`, `/questions/`, `/answers/?question=<id>`. Submission and speaker
  `code` are the external IDs.
- **Tito**: `../confdash/src/registration/tito_client.py` and
  `src/registration/services/tito_sync.py`. Tito API v3.1, keyed on account and event slug.
  M2 scope.
- **YouTube**: no existing code. New adapter.
