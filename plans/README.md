# Plans

Forward-looking work only. One file per substantial feature or area. Smaller items go in
`issues.md` (via `/issue`) and `nits.md` (via `/nit`). When work lands, prune or update the
relevant entry so this stays an accurate picture of what is left.

Reference material (things that are true, not things to do) belongs in `docs/`.

## Roadmap

| Milestone | Contents | Status |
| --- | --- | --- |
| M0 | Bootstrap: tooling, settings, tenancy foundation, provider abstraction | in progress |
| M1 | [Video review](video-review.md): Pretalx + YouTube adapters, organizer matching, speaker review, caption editing, publication | not started |
| M2 | [confdash port](confdash-port.md): registration/ticketing, sponsorship, dashboard, auth surfaces | not started |

M2 is deliberately not broken into shippable slices yet. It gets planned properly once M1
has landed and the provider abstraction has met a second real provider.

## Documents

- [data-model.md](data-model.md): tenancy, provider connections, program, video review models.
- [provider-integrations.md](provider-integrations.md): capability/adapter architecture, credential handling, sync semantics.
- [video-review.md](video-review.md): M1 detail.
- [confdash-port.md](confdash-port.md): M2 scope and inventory of the legacy project.
- [decisions.md](decisions.md): bootstrap decisions and their rationale, including what was deferred.
