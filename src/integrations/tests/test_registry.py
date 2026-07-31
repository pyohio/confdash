"""Provider registry tests.

The registry is what makes providers swappable, so its failure modes need to be loud: a
half-implemented adapter should fail at import, not at first sync against a real event.
"""

import pytest

from integrations import registry
from integrations.providers.base import Capability
from integrations.registry import ProviderNotRegistered
from integrations.tests.fakes import FakeTalkSource, FakeVideoHost, IncompleteTalkSource

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_registry():
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    try:
        yield
    finally:
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved)


def test_register_and_look_up(clean_registry):
    registry.register(FakeTalkSource)
    assert registry.get_adapter_class(Capability.TALK_SOURCE, "fake") is FakeTalkSource


def test_lookup_accepts_a_plain_string_capability(clean_registry):
    """Model fields store the capability as a string, so lookup must accept one."""
    registry.register(FakeTalkSource)
    assert registry.get_adapter_class("talk_source", "fake") is FakeTalkSource


def test_unknown_provider_raises_with_available_names(clean_registry):
    registry.register(FakeTalkSource)
    with pytest.raises(ProviderNotRegistered, match="fake"):
        registry.get_adapter_class(Capability.TALK_SOURCE, "sessionize")


def test_incomplete_adapter_is_rejected(clean_registry):
    """An adapter missing a protocol method must fail at registration."""
    with pytest.raises(TypeError, match="fetch_talks"):
        registry.register(IncompleteTalkSource)


def test_adapter_without_capability_is_rejected(clean_registry):
    class Nameless:
        pass

    with pytest.raises(TypeError, match="capability"):
        registry.register(Nameless)


def test_re_registering_the_same_class_is_idempotent(clean_registry):
    """Module re-import must not be an error."""
    registry.register(FakeTalkSource)
    registry.register(FakeTalkSource)
    assert registry.get_adapter_class(Capability.TALK_SOURCE, "fake") is FakeTalkSource


def test_conflicting_registration_is_rejected(clean_registry):
    registry.register(FakeTalkSource)

    class Impostor(FakeTalkSource):
        pass

    with pytest.raises(TypeError, match="already registered"):
        registry.register(Impostor)


def test_same_provider_name_across_capabilities_is_fine(clean_registry):
    """One vendor can supply several capabilities; the key is (capability, provider)."""
    registry.register(FakeTalkSource)
    registry.register(FakeVideoHost)
    assert registry.get_adapter_class(Capability.TALK_SOURCE, "fake") is FakeTalkSource
    assert registry.get_adapter_class(Capability.VIDEO_HOST, "fake") is FakeVideoHost


def test_providers_for_lists_only_that_capability(clean_registry):
    registry.register(FakeTalkSource)
    registry.register(FakeVideoHost)
    assert registry.providers_for(Capability.TALK_SOURCE) == ["fake"]
    assert registry.providers_for(Capability.TICKETING) == []


def test_all_providers_covers_every_capability(clean_registry):
    registry.register(FakeTalkSource)
    assert set(registry.all_providers()) == set(Capability)
