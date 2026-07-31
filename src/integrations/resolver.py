"""Turn an event plus a capability into a configured adapter.

The single entry point application code uses:

    adapter = resolve_adapter(event, Capability.TALK_SOURCE)
    talks = adapter.fetch_talks()

Every failure mode raises a typed error with an actionable message, rather than surfacing a bare
KeyError from inside a provider module. Misconfiguration is the expected case here — an organizer
setting up an event will get it wrong — so the errors are part of the interface.
"""

import structlog

from integrations.models import EventProviderBinding, ProviderConnection
from integrations.providers.base import Capability

logger = structlog.get_logger(__name__)


class IntegrationNotConfigured(Exception):
    """The event has no usable binding for this capability."""


def get_binding(event, capability: Capability | str) -> EventProviderBinding:
    """Return the active binding for an event and capability."""
    capability = Capability(capability)
    try:
        binding = EventProviderBinding.objects.select_related("connection", "event", "connection__organization").get(
            event=event, capability=capability
        )
    except EventProviderBinding.DoesNotExist as exc:
        raise IntegrationNotConfigured(
            f"{event} has no {capability} provider configured. Add an EventProviderBinding for it."
        ) from exc

    if not binding.is_active:
        raise IntegrationNotConfigured(f"The {capability} binding for {event} is inactive.")
    if not binding.connection.is_active:
        raise IntegrationNotConfigured(
            f"The connection {binding.connection.slug!r} used by {event} for {capability} is inactive."
        )
    return binding


def resolve_adapter(event, capability: Capability | str):
    """Instantiate the adapter an event is bound to for a capability.

    Config precedence: event-level binding config overrides connection-level config, so an
    organization sets defaults once and an event overrides only what differs.
    """
    capability = Capability(capability)
    binding = get_binding(event, capability)
    connection: ProviderConnection = binding.connection

    missing_config = binding.missing_config_keys()
    if missing_config:
        raise IntegrationNotConfigured(
            f"The {capability} binding for {event} is missing required config: {', '.join(missing_config)}"
        )

    missing_credentials = connection.missing_credential_keys()
    if missing_credentials:
        raise IntegrationNotConfigured(
            f"Connection {connection.slug!r} is missing required credentials: {', '.join(missing_credentials)}"
        )

    adapter_class = connection.adapter_class
    config = {**connection.config, **binding.config}

    logger.debug(
        "integrations.adapter_resolved",
        event_slug=event.slug,
        capability=str(capability),
        provider=connection.provider,
        connection_slug=connection.slug,
    )

    return adapter_class(config=config, credentials=connection.get_credentials())


def verify_connection(connection: ProviderConnection) -> tuple[bool, str]:
    """Run the adapter's credential check and record the outcome on the connection.

    Returns (succeeded, error_message). Never raises: this backs an admin action, where an
    unexpected provider exception should be reported, not turned into a 500.
    """
    try:
        missing = connection.missing_credential_keys()
        if missing:
            raise ValueError(f"Missing required credentials: {', '.join(missing)}")
        adapter = connection.adapter_class(config=connection.config, credentials=connection.get_credentials())
        adapter.check()
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        connection.mark_verified(error=message)
        logger.warning(
            "integrations.verify_failed",
            connection_slug=connection.slug,
            provider=connection.provider,
            error=message,
        )
        return False, message

    connection.mark_verified()
    logger.info("integrations.verify_ok", connection_slug=connection.slug, provider=connection.provider)
    return True, ""
