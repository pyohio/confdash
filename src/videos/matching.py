"""Suggesting which talk a video belongs to.

The videography team names uploads from the schedule, so titles broadly agree and the differences are
mechanical: underscores for spaces, stripped punctuation, different dashes, truncation, a trailing
qualifier. That makes this a normalization problem with a similarity tiebreak, not a matching problem.

Deterministic and dependency-free on purpose. `difflib` is stdlib, the same inputs always produce the
same ranking, and every score is explainable to an organizer wondering why a suggestion appeared. No
ML: there is nothing here a model would learn that the normalization does not already handle, and an
unexplainable ranking is worse than a slightly weaker one when a human confirms every match anyway.

Nothing here decides anything. It proposes an ordering; `videos.services` records what a human picks.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# Above this, a suggestion is safe to offer for bulk acceptance. Below it, a human should look. Chosen
# to sit above the observed spread between a real match and the next-best wrong answer; revisit against
# a real playlist rather than by intuition.
HIGH_CONFIDENCE = 0.90

# Noise the uploader or the file system adds, not part of any title.
_TRAILING_NOISE = re.compile(
    r"""
    (
        \s*\(\s*\d+\s*\)          # a "(1)" duplicate-download suffix
      | \s*-?\s*(final|edit(ed)?|v\d+|cut|master|processed|export)\b
      | \s*\[[^\]]*\]             # a bracketed qualifier
    )+\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_VIDEO_EXTENSIONS = re.compile(r"\.(mp4|mov|mkv|m4v|webm|avi)$", re.IGNORECASE)

# Words carrying no distinguishing power in a conference title, dropped only for the token comparison.
_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "to", "in", "for", "with", "on", "your", "you"})


def normalize(value: str) -> str:
    """Reduce a title to its comparable core.

    Order matters: strip the extension and trailing noise while separators are still recognizable, then
    flatten everything else.
    """
    if not value:
        return ""

    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _VIDEO_EXTENSIONS.sub("", text)
    text = text.replace("_", " ")
    # Any dash-like or slash-like character becomes a space, so "Python-Powered" and "Python Powered"
    # agree. Includes the en and em dashes a CFP form will happily accept.
    text = re.sub(r"[-–—/\\|:;,]+", " ", text)
    text = text.lower()
    text = _TRAILING_NOISE.sub("", text)
    # Everything that is not a word character or space goes, which covers apostrophes, quotes, question
    # marks, and the emoji that occasionally reach a talk title.
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse(value: str) -> str:
    """`normalize` with the spaces removed too.

    Uploaders are inconsistent about compound words: a schedule reading "Code-Scape" becomes
    "CodeScape" in a filename as readily as "Code Scape". Comparing the collapsed forms as well means a
    dropped separator costs nothing, where treating every hyphen as a space would score it as a
    difference.

    Grounded in the 2025 corpus, where exactly this cost the only real mismatch.
    """
    return normalize(value).replace(" ", "")


def _tokens(normalized: str) -> frozenset[str]:
    return frozenset(normalized.split()) - _STOPWORDS


def score(video_title: str, talk_title: str) -> float:
    """Similarity between a video title and a talk title, from 0.0 to 1.0.

    Two signals, combined rather than chosen between:

    - Sequence similarity, which rewards the whole string agreeing and punishes rearrangement.
    - Token overlap, which survives truncation and reordering, and is what rescues a filename that
      dropped a subtitle the schedule kept.

    Weighted toward sequence similarity because it is the stronger signal when both titles are complete,
    with token overlap doing the work when one has been cut short.
    """
    left, right = normalize(video_title), normalize(talk_title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_collapsed, right_collapsed = collapse(video_title), collapse(talk_title)
    if left_collapsed == right_collapsed:
        # Identical once separators are ignored, e.g. "Code-Scape" against "CodeScape".
        return 1.0

    # The better of the two readings: a separator the uploader dropped should not read as a difference.
    sequence = max(
        SequenceMatcher(None, left, right).ratio(),
        SequenceMatcher(None, left_collapsed, right_collapsed).ratio(),
    )

    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if left_tokens and right_tokens:
        shared = len(left_tokens & right_tokens)
        # Against the smaller side, so a truncated filename still scores well against the full title
        # instead of being penalized for the words it lost.
        overlap = shared / min(len(left_tokens), len(right_tokens))
    else:
        overlap = 0.0

    return round(0.6 * sequence + 0.4 * overlap, 4)


@dataclass(frozen=True)
class Suggestion:
    talk: object
    score: float

    @property
    def is_high_confidence(self) -> bool:
        return self.score >= HIGH_CONFIDENCE


def suggest(video_title: str, talks, *, limit: int = 5, minimum: float = 0.35) -> list[Suggestion]:
    """Rank `talks` against a video title, best first.

    `minimum` drops the long tail of unrelated talks so an organizer sees a few plausible options rather
    than the whole programme. Ties break on talk title so the ordering is stable across calls, which
    matters because an unstable list reorders under the cursor.
    """
    scored = [Suggestion(talk=talk, score=score(video_title, talk.title)) for talk in talks]
    candidates = [s for s in scored if s.score >= minimum]
    candidates.sort(key=lambda s: (-s.score, s.talk.title))
    return candidates[:limit]


def best_match(video_title: str, talks, *, minimum: float = 0.35) -> Suggestion | None:
    """The single best candidate, or None if nothing clears `minimum`."""
    ranked = suggest(video_title, talks, limit=1, minimum=minimum)
    return ranked[0] if ranked else None


def is_unambiguous(suggestions: list[Suggestion], *, margin: float = 0.12) -> bool:
    """Whether the top suggestion is clearly ahead of the runner-up.

    High confidence alone is not enough to auto-accept. Two talks in a series with near-identical titles
    can both score above the threshold, and picking the higher one would be a coin toss that looks like
    a decision. A clear margin is what makes bulk acceptance safe.
    """
    if not suggestions:
        return False
    if not suggestions[0].is_high_confidence:
        return False
    if len(suggestions) == 1:
        return True
    return suggestions[0].score - suggestions[1].score >= margin
