"""Provider adapters.

Importing this package registers every adapter. `IntegrationsConfig.ready()` imports it, so the
registry is populated before anything asks for an adapter.

Concrete adapters arrive with M1: `pretalx` (talk_source) and `youtube` (video_host). Add the
module, decorate the class with `@register`, and import it below — no migration, and no change to
application code.
"""
