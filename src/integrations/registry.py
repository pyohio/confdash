"""Provider registry: maps (capability, provider) to an adapter class.

Adapters register themselves via the decorator, so adding a provider means adding a module under
`providers/` and importing it here. No migration, no change to application code.
"""

from integrations.providers.base import CAPABILITY_PROTOCOLS, Capability


class ProviderNotRegistered(Exception):
    """No adapter is registered for this capability and provider name."""


_REGISTRY: dict[tuple[Capability, str], type] = {}


def register(adapter_class: type) -> type:
    """Class decorator that adds an adapter to the registry.

    Validates at import time that the adapter declares a known capability and actually satisfies
    that capability's protocol, so a half-implemented adapter fails on startup rather than at
    first sync.
    """
    capability = getattr(adapter_class, "capability", None)
    provider = getattr(adapter_class, "provider", None)

    if capability is None or provider is None:
        raise TypeError(f"{adapter_class.__name__} must define both `capability` and `provider`.")

    capability = Capability(capability)
    protocol = CAPABILITY_PROTOCOLS[capability]

    # `issubclass` against a Protocol with non-method members raises TypeError, and these
    # protocols declare attributes as well as methods. Checking `__protocol_attrs__` presence
    # directly catches the mistake that actually happens: a missing or misspelled method.
    missing = sorted(name for name in protocol.__protocol_attrs__ if not hasattr(adapter_class, name))
    if missing:
        raise TypeError(f"{adapter_class.__name__} does not satisfy the {capability} protocol. Missing: {missing}")

    key = (capability, provider)
    if key in _REGISTRY and _REGISTRY[key] is not adapter_class:
        raise TypeError(f"A different adapter is already registered for {capability}/{provider}.")

    _REGISTRY[key] = adapter_class
    return adapter_class


def get_adapter_class(capability: Capability | str, provider: str) -> type:
    """Return the adapter class for a capability and provider name."""
    capability = Capability(capability)
    try:
        return _REGISTRY[(capability, provider)]
    except KeyError as exc:
        available = sorted(p for c, p in _REGISTRY if c == capability)
        raise ProviderNotRegistered(
            f"No {capability} provider named {provider!r}. Available: {available or 'none'}"
        ) from exc


def providers_for(capability: Capability | str) -> list[str]:
    """Provider names registered for a capability. Backs admin choice fields."""
    capability = Capability(capability)
    return sorted(p for c, p in _REGISTRY if c == capability)


def all_providers() -> dict[Capability, list[str]]:
    return {capability: providers_for(capability) for capability in Capability}


def _reset_for_tests() -> None:
    """Clear the registry. Tests that register fake adapters use this to avoid leaking state."""
    _REGISTRY.clear()
