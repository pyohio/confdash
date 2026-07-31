# Issues

> Smaller TODOs and problems that do not warrant their own plan file.

- **Credential key rotation.** `FIELD_ENCRYPTION_KEY` has no rotation path. Needs a
  `rotate_credentials` management command that re-encrypts every `ProviderConnection` under a
  new key, and a documented procedure. Not urgent until a second org is onboarded, but it is a
  real operational gap once the app holds tenant secrets.
- **Deployment target undecided**, pending a cost comparison of reusing the legacy DigitalOcean
  droplet versus Fly.io. Blocks M1.4 onward, since a magic link needs a real hostname. See
  `decisions.md`.
- **Verify confdash.org with Mailgun** (SPF/DKIM DNS records) before M1.4 sends anything. Until
  then mail from that domain will be rejected.
- **Confirm PyOhio 2026 placeholder title convention** before tuning the M1.3 matcher.
- **Measure YouTube Data API quota cost** against a real playlist during M1.2.
