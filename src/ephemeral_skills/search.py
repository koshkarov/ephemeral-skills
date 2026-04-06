"""Keyword search engine for skills catalog."""

from __future__ import annotations

import math
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

# Query coverage bonus: multiplier when a skill matches a high fraction of query tokens
COVERAGE_BONUS_FULL = 1.25  # skill matches every query token
COVERAGE_BONUS_NONE = 1.0   # no bonus at minimum


def stem(token: str) -> str:
    """Lightweight suffix stripping for search normalization.

    Collapses plurals, gerunds, past tense, and agent nouns so that
    'presentations' matches 'presentation', 'testing' matches 'test', etc.
    Applied sequentially: strip plural -s first, then one derivational suffix,
    then trailing -e for consistency (create/creating both → 'creat').
    """
    original = token
    # 1. Plural -s (but not -ss like "process")
    if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
        token = token[:-1]
    # 2. One derivational suffix (mutually exclusive)
    if token.endswith("ing") and len(token) > 5:
        token = token[:-3]
    elif token.endswith("ed") and len(token) > 4:
        token = token[:-2]
    elif token.endswith("er") and len(token) > 5:
        token = token[:-2]
    elif token.endswith("ly") and len(token) > 5:
        token = token[:-2]
    # 3. Collapse doubled trailing consonant ONLY if suffix stripping created it
    #    (e.g. "debugging" → "debugg" → "debug", but not "skill" → "skil")
    if (
        token != original
        and len(token) >= 3
        and token[-1] == token[-2]
        and token[-1] not in "aeiou"
    ):
        token = token[:-1]
    # 4. Trailing -e for create/creat, slide/slid consistency
    if token.endswith("e") and len(token) > 4:
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens, filtering stop words and short tokens."""
    return [
        stem(t)
        for t in TOKEN_RE.findall(text.lower())
        if t not in STOP_WORDS and len(t) >= MIN_TOKEN_LEN
    ]


@dataclass
class SearchResult:
    skill: Skill
    score: float


def _build_idf(skills: list[Skill]) -> dict[str, float]:
    """Compute inverse document frequency for each token across all skills.

    IDF = log(N / df) where df = number of skills containing the token.
    Tokens appearing in every skill get IDF ≈ 0; rare tokens get higher IDF.
    We use log(N / df) + 1 so that even universal tokens contribute a baseline of 1.0.
    """
    n = len(skills)
    if n == 0:
        return {}

    doc_freq: dict[str, int] = {}
    for skill in skills:
        # Union of all tokens across the three searchable fields
        tokens = set(tokenize(skill.name.replace("-", " ")))
        tokens |= set(tokenize(skill.description))
        tokens |= set(tokenize(skill.tags))
        for t in tokens:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    return {t: math.log(n / df) + 1.0 for t, df in doc_freq.items()}


def _score_field(
    query_tokens: list[str],
    field_tokens: set[str],
    exact_w: float,
    partial_w: float,
    idf: dict[str, float],
) -> tuple[float, int]:
    """Score query tokens against a set of field tokens.

    Returns (score, matched_count) where matched_count is the number of
    distinct query tokens that hit this field.
    """
    score = 0.0
    matched = 0
    for qt in query_tokens:
        qt_idf = idf.get(qt, 1.0)
        if qt in field_tokens:
            score += exact_w * qt_idf
            matched += 1
        elif len(qt) >= MIN_SUBSTRING_LEN:
            # Partial/substring: only for tokens long enough to be meaningful
            for ft in field_tokens:
                if len(ft) >= MIN_SUBSTRING_LEN and (qt in ft or ft in qt):
                    score += partial_w * qt_idf
                    matched += 1
                    break
    return score, matched


def search(skills: list[Skill], query: str, limit: int = 10) -> list[SearchResult]:
    """Search skills by keyword query. Returns results ranked by relevance score."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    idf = _build_idf(skills)

    results: list[SearchResult] = []
    n_query = len(set(query_tokens))  # unique query tokens

    for skill in skills:
        name_tokens = set(tokenize(skill.name.replace("-", " ")))
        desc_tokens = set(tokenize(skill.description))
        tag_tokens = set(tokenize(skill.tags))

        score = 0.0
        matched_tokens: set[str] = set()

        for field_tokens, exact_w, partial_w in [
            (name_tokens, NAME_EXACT_WEIGHT, NAME_PARTIAL_WEIGHT),
            (desc_tokens, DESCRIPTION_EXACT_WEIGHT, DESCRIPTION_PARTIAL_WEIGHT),
            (tag_tokens, TAGS_EXACT_WEIGHT, TAGS_PARTIAL_WEIGHT),
        ]:
            field_score, _ = _score_field(query_tokens, field_tokens, exact_w, partial_w, idf)
            score += field_score

            # Track which query tokens matched (for coverage)
            for qt in query_tokens:
                if qt in field_tokens:
                    matched_tokens.add(qt)
                elif len(qt) >= MIN_SUBSTRING_LEN:
                    for ft in field_tokens:
                        if len(ft) >= MIN_SUBSTRING_LEN and (qt in ft or ft in qt):
                            matched_tokens.add(qt)
                            break

        if score > 0:
            # Query coverage bonus: reward skills that match more of the query
            coverage = len(matched_tokens) / n_query if n_query > 0 else 0
            coverage_multiplier = COVERAGE_BONUS_NONE + (COVERAGE_BONUS_FULL - COVERAGE_BONUS_NONE) * coverage
            score *= coverage_multiplier

            results.append(SearchResult(skill=skill, score=score))

    # Sort by score descending, then skill name ascending as a stable tiebreaker
    results.sort(key=lambda r: (-r.score, r.skill.name))
    return results[:limit]
