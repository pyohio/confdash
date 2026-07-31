# Issues

> Smaller TODOs and problems that do not warrant their own plan file.

- **Credential key rotation.** `FIELD_ENCRYPTION_KEY` has no rotation path. Needs a
  `rotate_credentials` management command that re-encrypts every `ProviderConnection` under a
  new key, and a documented procedure. Not urgent until a second org is onboarded, but it is a
  real operational gap once the app holds tenant secrets.
- **Deployment target undecided.** Blocks M1.4 (magic links need real outbound email and a
  hostname). See `decisions.md`.
- **Confirm PyOhio 2026 placeholder title convention** before tuning the M1.3 matcher.
- **Measure YouTube Data API quota cost** against a real playlist during M1.2.
