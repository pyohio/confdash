"""Provider adapters.

Importing this package registers every adapter. `IntegrationsConfig.ready()` imports it, so the
registry is populated before anything asks for an adapter.

Adding a provider: add the module, decorate the class with `@register`, and import it below. No
migration, and no change to application code. `youtube` (video_host) arrives with M1.2.
"""

from integrations.providers import pretalx  # noqa: F401 — imported for its @register side effect
