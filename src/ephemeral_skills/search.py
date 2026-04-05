"""Keyword search engine for skills catalog."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import Skill

# Words too common to be useful as search terms
STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into", "about",
    "that", "this", "it", "its", "or", "and", "but", "not", "no", "if",
    "so", "than", "too", "very", "just", "also", "when", "how", "what",
    "which", "who", "whom", "where", "why", "all", "each", "any", "some",
    "my", "your", "i", "me", "we", "you", "they", "them", "he", "she",
    "use", "using", "used",
})

# Weights for where a match occurs
NAME_EXACT_WEIGHT = 10.0
NAME_PARTIAL_WEIGHT = 5.0
DESCRIPTION_EXACT_WEIGHT = 3.0
DESCRIPTION_PARTIAL_WEIGHT = 1.5
TAGS_EXACT_WEIGHT = 4.0
TAGS_PARTIAL_WEIGHT = 2.0

TOKEN_RE = re.compile(r"[a-z0-9]+")

# Minimum token length — single chars produce too many false substring matches
MIN_TOKEN_LEN = 2

# Minimum length for substring matching — short tokens like "go" shouldn't
# partially match "algorithm"
MIN_SUBSTRING_LEN = 3


def tokenize(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens, filtering stop words and short tokens."""
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP_WORDS and len(t) >= MIN_TOKEN_LEN]


@dataclass
class SearchResult:
    skill: Skill
    score: float


def _score_field(query_tokens: list[str], field_tokens: set[str], exact_w: float, partial_w: float) -> float:
    """Score query tokens against a set of field tokens."""
    score = 0.0
    for qt in query_tokens:
        if qt in field_tokens:
            score += exact_w
        elif len(qt) >= MIN_SUBSTRING_LEN:
            # Partial/substring: only for tokens long enough to be meaningful
            for ft in field_tokens:
                if len(ft) >= MIN_SUBSTRING_LEN and (qt in ft or ft in qt):
                    score += partial_w
                    break
    return score


def search(skills: list[Skill], query: str, limit: int = 10) -> list[SearchResult]:
    """Search skills by keyword query. Returns results ranked by relevance score."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    results: list[SearchResult] = []
    for skill in skills:
        name_tokens = set(tokenize(skill.name.replace("-", " ")))
        desc_tokens = set(tokenize(skill.description))
        tag_tokens = set(tokenize(skill.tags))

        score = 0.0
        score += _score_field(query_tokens, name_tokens, NAME_EXACT_WEIGHT, NAME_PARTIAL_WEIGHT)
        score += _score_field(query_tokens, desc_tokens, DESCRIPTION_EXACT_WEIGHT, DESCRIPTION_PARTIAL_WEIGHT)
        score += _score_field(query_tokens, tag_tokens, TAGS_EXACT_WEIGHT, TAGS_PARTIAL_WEIGHT)

        if score > 0:
            results.append(SearchResult(skill=skill, score=score))

    # Sort by score descending, then skill name ascending as a stable tiebreaker
    results.sort(key=lambda r: (-r.score, r.skill.name))
    return results[:limit]
