"""The Scope enum itself. No database."""

import pytest

from events.scopes import Scope

pytestmark = pytest.mark.unit


def test_every_scope_has_a_label():
    """Labels drive the organizer UI, so a member added without one would raise at render time."""
    for scope in Scope:
        assert scope.label


def test_choices_pairs_values_with_labels():
    assert ("videos", Scope.VIDEOS.label) in Scope.choices()
    assert len(Scope.choices()) == len(list(Scope))


def test_values_returns_the_raw_strings():
    assert Scope.values() == {"program", "videos", "sponsorship", "comms"}


def test_scopes_compare_equal_to_their_strings():
    """StrEnum, so stored JSON values and enum members are interchangeable in comparisons."""
    assert Scope.PROGRAM == "program"
    assert "program" in {Scope.PROGRAM}
