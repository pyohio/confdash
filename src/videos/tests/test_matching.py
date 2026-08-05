"""Title matching.

No database: this is pure string work, which is the point of keeping it out of the service layer.

The cases are drawn from what the 2025 uploads actually looked like — underscored filenames, dropped
punctuation, a `(1)` duplicate suffix — rather than from imagined variance.
"""

from dataclasses import dataclass

import pytest

from videos.matching import (
    HIGH_CONFIDENCE,
    Suggestion,
    best_match,
    collapse,
    is_unambiguous,
    normalize,
    score,
    suggest,
)

pytestmark = pytest.mark.unit


@dataclass
class FakeTalk:
    title: str


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Deploy_Django_GitOps_Kubernetes_Made_Easy.mp4", "deploy django gitops kubernetes made easy"),
            ("Deploy Django: GitOps + Kubernetes Made Easy", "deploy django gitops kubernetes made easy"),
            ("  Taming   Async  Python  ", "taming async python"),
            ("Python's Best Feature?", "pythons best feature"),
            # A slash separates words, so it becomes a space rather than closing up.
            ("Async/Await — A Deep Dive", "async await a deep dive"),
        ],
    )
    def test_reduces_titles_to_a_comparable_core(self, raw, expected):
        assert normalize(raw) == expected

    def test_strips_a_duplicate_download_suffix(self):
        """`originals/` held exactly this: the same talk downloaded twice."""
        assert normalize("Bringing_Ideas_to_Life (1).mp4") == normalize("Bringing Ideas to Life")

    def test_strips_editing_qualifiers(self):
        assert normalize("Lights_Python_Action-final.mp4") == normalize("Lights, Python, Action")
        assert normalize("Some Talk [processed].mov") == normalize("Some Talk")

    def test_folds_accents_rather_than_dropping_them(self):
        assert normalize("Café Culture") == "cafe culture"

    def test_an_empty_title_normalizes_to_empty(self):
        assert normalize("") == ""
        assert normalize("   ") == ""


class TestScore:
    def test_identical_titles_score_one(self):
        assert score("Taming Async Python", "Taming Async Python") == 1.0

    def test_formatting_differences_alone_score_one(self):
        """The whole premise: separators and punctuation must not cost anything."""
        assert (
            score("Deploy_Django_GitOps_Kubernetes_Made_Easy.mp4", "Deploy Django: GitOps Kubernetes Made Easy") == 1.0
        )

    def test_unrelated_titles_score_low(self):
        assert score("Quantum Computing with Python", "Sponsor Lunch Announcements") < 0.35

    def test_a_truncated_filename_still_scores_well(self):
        """Token overlap against the smaller side is what rescues this."""
        assert score("Beyond_the_Black_Box.mp4", "Beyond the Black Box: Interpreting ML Models with SHAP") > 0.6

    def test_an_empty_side_scores_zero(self):
        assert score("", "A Talk") == 0.0
        assert score("A Talk", "") == 0.0

    def test_scores_are_symmetric_enough_to_be_predictable(self):
        a = score("Lights Python Action", "Lights, Python, Action!")
        b = score("Lights, Python, Action!", "Lights Python Action")
        assert a == b


class TestSuggest:
    @pytest.fixture
    def talks(self):
        return [
            FakeTalk("Deploy Django: GitOps Kubernetes Made Easy"),
            FakeTalk("Quantum Computing with Python: From Qubits to Circuits"),
            FakeTalk("uv: Ultimate Victory over Installation and Dependency Chaos"),
            FakeTalk("Lights, Python, Action!"),
        ]

    def test_ranks_the_right_talk_first(self, talks):
        ranked = suggest("Deploy_Django_GitOps_Kubernetes_Made_Easy.mp4", talks)

        assert ranked[0].talk.title == "Deploy Django: GitOps Kubernetes Made Easy"
        assert ranked[0].is_high_confidence

    def test_drops_the_unrelated_tail(self, talks):
        """An organizer should see a few plausible options, not the whole programme."""
        ranked = suggest("Lights_Python_Action.mp4", talks)

        assert len(ranked) < len(talks)
        assert ranked[0].talk.title == "Lights, Python, Action!"

    def test_returns_nothing_for_a_video_matching_no_talk(self, talks):
        """A welcome or closing remarks should produce no suggestion at all, not a weak one."""
        assert suggest("sunday_welcome.mp4", talks) == []
        assert suggest("closing_remarks.mp4", talks) == []

    def test_respects_the_limit(self, talks):
        assert len(suggest("python", talks, limit=2, minimum=0.0)) == 2

    def test_ordering_is_stable_for_equal_scores(self, talks):
        """An unstable list reorders under the organizer's cursor."""
        first = [s.talk.title for s in suggest("python", talks, minimum=0.0, limit=99)]
        second = [s.talk.title for s in suggest("python", talks, minimum=0.0, limit=99)]
        assert first == second

    def test_best_match_returns_the_top_candidate(self, talks):
        assert best_match("uv_Ultimate_Victory.mp4", talks).talk.title.startswith("uv:")

    def test_best_match_returns_none_below_the_floor(self, talks):
        assert best_match("closing_remarks.mp4", talks) is None


class TestIsUnambiguous:
    def test_a_clear_leader_is_unambiguous(self):
        suggestions = [Suggestion(talk=FakeTalk("A"), score=0.98), Suggestion(talk=FakeTalk("B"), score=0.50)]
        assert is_unambiguous(suggestions) is True

    def test_a_lone_high_confidence_suggestion_is_unambiguous(self):
        assert is_unambiguous([Suggestion(talk=FakeTalk("A"), score=0.97)]) is True

    def test_two_near_identical_scores_are_ambiguous(self):
        """Both clear the threshold, so picking the higher one is a coin toss dressed as a decision.

        This is the case that makes bulk-accept dangerous without a margin check: a talk series with
        near-identical titles.
        """
        suggestions = [
            Suggestion(talk=FakeTalk("Part One"), score=0.96),
            Suggestion(talk=FakeTalk("Part Two"), score=0.95),
        ]
        assert is_unambiguous(suggestions) is False

    def test_a_low_scoring_leader_is_ambiguous(self):
        assert is_unambiguous([Suggestion(talk=FakeTalk("A"), score=HIGH_CONFIDENCE - 0.01)]) is False

    def test_no_suggestions_is_ambiguous(self):
        assert is_unambiguous([]) is False


class TestDroppedSeparators:
    """The one real mismatch in the 2025 corpus, and why `collapse` exists."""

    def test_a_dropped_hyphen_costs_nothing(self):
        assert (
            score("Organizing_and_Maintaining_Your_CodeScape.mp4", "Organizing and Maintaining Your Code-Scape") == 1.0
        )

    def test_collapse_removes_separators_entirely(self):
        assert collapse("Code-Scape Tools") == "codescapetools"

    def test_a_dropped_space_costs_nothing(self):
        assert score("WebAssembly_Basics.mp4", "Web Assembly Basics") == 1.0

    def test_collapsing_does_not_match_genuinely_different_titles(self):
        """The relaxation must not turn unrelated talks into candidates."""
        assert score("Quantum_Computing_with_Python.mp4", "Sponsor Lunch Announcements") < 0.35
