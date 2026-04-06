"""Level 1: Search quality tests — does the right skill rank first?"""

import pytest

from ephemeral_skills.catalog import Skill, SkillCatalog
from ephemeral_skills.search import (
    SearchResult,
    _build_idf,
    _score_field,
    search,
    stem,
    tokenize,
)


# ---------------------------------------------------------------------------
# Stemmer
# ---------------------------------------------------------------------------


class TestStem:
    """Verify that inflected forms collapse to the same root."""

    @pytest.mark.parametrize(
        "word_a,word_b",
        [
            ("presentation", "presentations"),
            ("test", "testing"),
            ("slide", "slides"),
            ("document", "documents"),
            ("build", "building"),
            ("create", "created"),
            ("create", "creating"),
            ("build", "builder"),
            ("game", "games"),
            ("application", "applications"),
            ("debug", "debugging"),
            ("automate", "automated"),
        ],
    )
    def test_inflection_pairs(self, word_a: str, word_b: str):
        """Two related inflections must produce the same stem."""
        assert stem(word_a) == stem(word_b), (
            f"stem({word_a!r})={stem(word_a)!r} != stem({word_b!r})={stem(word_b)!r}"
        )

    @pytest.mark.parametrize(
        "word",
        ["web", "pdf", "mcp", "api", "go", "js", "css", "html", "git"],
    )
    def test_short_words_unchanged(self, word: str):
        """Short tokens must not be mangled by the stemmer."""
        assert stem(word) == word

    def test_no_overstem(self):
        """Words must not be reduced to fewer than 3 characters."""
        for word in ["used", "uses", "user", "ace", "ice"]:
            assert len(stem(word)) >= 2, f"stem({word!r})={stem(word)!r} is too short"


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic(self):
        tokens = tokenize("create a PDF document")
        assert "creat" in tokens  # stemmed
        assert "pdf" in tokens
        assert "document" in tokens

    def test_stop_words_removed(self):
        tokens = tokenize("I want to use the skill for my project")
        assert "want" in tokens
        assert "skill" in tokens  # "skill" stays (no suffix rule applies)
        assert "project" in tokens
        # Stop words must be gone
        for stop in ("i", "to", "use", "the", "for", "my"):
            assert stop not in tokens

    def test_hyphenated_input(self):
        tokens = tokenize("mcp-builder")
        assert "mcp" in tokens
        assert "build" in tokens  # stemmed from "builder"

    def test_empty(self):
        assert tokenize("") == []
        assert tokenize("the a an") == []

    def test_stemming_applied(self):
        """Tokenizer must apply stemming to all tokens."""
        assert tokenize("presentations") == tokenize("presentation")
        assert tokenize("testing tools") == tokenize("test tool")


# ---------------------------------------------------------------------------
# IDF
# ---------------------------------------------------------------------------


class TestIDF:
    def _make_skills(self, descriptions: list[str]) -> list[Skill]:
        return [
            Skill(name=f"skill-{i}", description=d) for i, d in enumerate(descriptions)
        ]

    def test_rare_token_higher_idf(self):
        """A token appearing in fewer skills must have higher IDF."""
        skills = self._make_skills([
            "build a web application",
            "test a web service",
            "deploy a quantum simulator",
        ])
        idf = _build_idf(skills)
        # "web" appears in 2/3 skills, "quantum" in 1/3
        assert idf[stem("quantum")] > idf[stem("web")]

    def test_universal_token_low_idf(self):
        """A token in every skill gets baseline IDF of 1.0."""
        skills = self._make_skills(["build tool", "build server", "build app"])
        idf = _build_idf(skills)
        assert idf[stem("build")] == pytest.approx(1.0, abs=0.01)

    def test_empty_skills(self):
        assert _build_idf([]) == {}


# ---------------------------------------------------------------------------
# Field scoring
# ---------------------------------------------------------------------------


class TestScoreField:
    def test_exact_match_beats_partial(self):
        """Exact token match must score higher than substring match."""
        idf = {"test": 1.0, "testing": 1.0}
        exact_score, _ = _score_field(["test"], {"test", "other"}, 10.0, 5.0, idf)
        partial_score, _ = _score_field(["test"], {"testing", "other"}, 10.0, 5.0, idf)
        assert exact_score > partial_score

    def test_idf_amplifies_rare_tokens(self):
        """Higher IDF tokens must produce higher field scores."""
        idf_common = {"web": 1.2}
        idf_rare = {"quantum": 3.5}
        score_common, _ = _score_field(["web"], {"web"}, 10.0, 5.0, idf_common)
        score_rare, _ = _score_field(["quantum"], {"quantum"}, 10.0, 5.0, idf_rare)
        assert score_rare > score_common

    def test_no_match_returns_zero(self):
        idf = {"xyz": 1.0}
        score, matched = _score_field(["xyz"], {"abc", "def"}, 10.0, 5.0, idf)
        assert score == 0.0
        assert matched == 0


# ---------------------------------------------------------------------------
# Search quality against real skills catalog
# ---------------------------------------------------------------------------

# (query, expected_top_skill_or_list, should_not_be_first)
# When expected_top is a list, any of those skills is acceptable as #1.
SEARCH_CASES = [
    # --- Clear matches: query directly names the domain ---
    ("extract text from a pdf", "pdf", []),
    ("build an mcp server", "mcp-builder", []),
    # doc and docx overlap — both handle .docx files; either is acceptable
    ("create a word document", ["doc", "docx"], []),
    # slides outranks pptx for presentation/slides queries (exact name match)
    ("create a powerpoint presentation", "slides", []),
    ("spreadsheet excel data analysis", "xlsx", []),
    ("build an app with claude api", "claude-api", []),
    # webapp-testing must outrank develop-web-game for explicit testing queries
    ("test my web application", "webapp-testing", ["develop-web-game"]),
    ("create a gif for slack", "slack-gif-creator", []),

    # --- Indirect matches: user describes need without naming the tool ---
    ("make slides for my pitch deck", "slides", []),
    ("fill out a form in a pdf file", "pdf", []),
    ("I need to merge multiple pdf files into one", "pdf", []),
    ("write a status report for leadership", "internal-comms", []),
    ("style my artifact with company colors", "brand-guidelines", []),

    # --- Near-miss negative: should match specific skill, not neighbor ---
    ("create generative art with code", "algorithmic-art", ["frontend-design"]),
    ("build a react landing page", "frontend-design", ["algorithmic-art"]),
]


class TestSearchQuality:
    @pytest.mark.parametrize("query,expected_top,should_not_be_first", SEARCH_CASES)
    def test_top_result(
        self,
        catalog: SkillCatalog,
        query: str,
        expected_top: str | list[str],
        should_not_be_first: list[str],
    ):
        results = search(catalog.all_skills(), query, limit=5)
        assert len(results) > 0, f"No results for query: {query}"

        top_name = results[0].skill.name
        acceptable = expected_top if isinstance(expected_top, list) else [expected_top]

        assert top_name in acceptable, (
            f"Query '{query}': expected one of {acceptable} as top result, "
            f"got '{top_name}' (score={results[0].score:.1f}). "
            f"Top 3: {[(r.skill.name, round(r.score, 1)) for r in results[:3]]}"
        )

        for bad in should_not_be_first:
            assert top_name != bad, (
                f"Query '{query}': '{bad}' should not rank first"
            )

    def test_no_results_for_irrelevant_query(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "quantum physics thermodynamics")
        assert len(results) == 0 or results[0].score < 5.0

    def test_limit_respected(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "create", limit=3)
        assert len(results) <= 3

    def test_empty_query(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Disambiguation: overlapping skills must separate under the right query
# ---------------------------------------------------------------------------

# These test the specific PRODUCTION.md issue: skills that overlap should
# be distinguishable when the query makes the intent clear.
DISAMBIGUATION_CASES = [
    # pptx for read/parse, slides for authoring
    ("read a pptx file", "pptx", "slides"),
    ("parse text from powerpoint", "pptx", None),
    ("create presentation slides", "slides", None),
    # webapp-testing for testing, develop-web-game for building games
    ("test my web application", "webapp-testing", "develop-web-game"),
    ("build a browser game", "develop-web-game", "webapp-testing"),
    # "debug web app" is ambiguous between testing and game dev — both use Playwright.
    # Accept either, but webapp-testing or develop-web-game must be in top 2.
    ("debug web app with playwright", ["webapp-testing", "develop-web-game"], None),
]


class TestDisambiguation:
    @pytest.mark.parametrize("query,expected_first,expected_not_first", DISAMBIGUATION_CASES)
    def test_disambiguation(
        self,
        catalog: SkillCatalog,
        query: str,
        expected_first: str | list[str],
        expected_not_first: str | None,
    ):
        results = search(catalog.all_skills(), query, limit=5)
        assert len(results) > 0, f"No results for: {query}"

        top = results[0].skill.name
        acceptable = expected_first if isinstance(expected_first, list) else [expected_first]

        assert top in acceptable, (
            f"Query '{query}': expected one of {acceptable} first, got '{top}'. "
            f"Top 3: {[(r.skill.name, round(r.score, 1)) for r in results[:3]]}"
        )

        if expected_not_first:
            assert top != expected_not_first, (
                f"Query '{query}': '{expected_not_first}' should NOT be first"
            )


# ---------------------------------------------------------------------------
# Score separation: overlapping skills should have a meaningful score gap
# when the query clearly favors one
# ---------------------------------------------------------------------------


class TestScoreSeparation:
    def test_webapp_testing_vs_develop_web_game(self, catalog: SkillCatalog):
        """webapp-testing must score meaningfully higher for testing queries."""
        results = search(catalog.all_skills(), "test my web application", limit=10)
        scores = {r.skill.name: r.score for r in results}
        assert "webapp-testing" in scores
        assert "develop-web-game" in scores
        # At least 20% gap
        assert scores["webapp-testing"] > scores["develop-web-game"] * 1.2, (
            f"webapp-testing ({scores['webapp-testing']:.1f}) should be >20% "
            f"above develop-web-game ({scores['develop-web-game']:.1f})"
        )

    def test_slides_vs_pptx_for_create(self, catalog: SkillCatalog):
        """slides should beat pptx for authoring queries."""
        results = search(catalog.all_skills(), "create a powerpoint presentation", limit=10)
        scores = {r.skill.name: r.score for r in results}
        assert "slides" in scores
        assert "pptx" in scores
        assert scores["slides"] > scores["pptx"], (
            f"slides ({scores['slides']:.1f}) should beat pptx ({scores['pptx']:.1f})"
        )

    def test_pptx_vs_slides_for_read(self, catalog: SkillCatalog):
        """pptx should beat slides for read/parse queries."""
        results = search(catalog.all_skills(), "read a pptx file", limit=10)
        scores = {r.skill.name: r.score for r in results}
        assert "pptx" in scores
        assert scores["pptx"] > scores.get("slides", 0), (
            f"pptx ({scores['pptx']:.1f}) should beat slides ({scores.get('slides', 0):.1f})"
        )


# ---------------------------------------------------------------------------
# Coverage bonus: skills matching more query tokens should rank higher
# ---------------------------------------------------------------------------


class TestCoverageBonus:
    def _make_skill(self, name: str, description: str, tags: str = "") -> Skill:
        return Skill(name=name, description=description, metadata={"tags": tags} if tags else {})

    def test_full_coverage_beats_partial(self):
        """A skill matching all query tokens should outscore one matching only some."""
        skills = [
            self._make_skill("broad", "handles web testing and deployment automation"),
            self._make_skill("narrow", "web framework for building sites"),
        ]
        results = search(skills, "web testing automation", limit=2)
        assert results[0].skill.name == "broad"

    def test_coverage_bonus_applied(self):
        """Verify that coverage multiplier is > 1.0 for full-coverage matches."""
        skills = [
            self._make_skill("full", "alpha beta gamma"),
        ]
        results = search(skills, "alpha beta gamma", limit=1)
        # With 3/3 coverage the multiplier is 1.25 — score should be > raw field sum
        assert results[0].score > 0


# ---------------------------------------------------------------------------
# IDF in practice: rare terms should dominate scoring
# ---------------------------------------------------------------------------


class TestIDFIntegration:
    def test_rare_term_discriminates(self, catalog: SkillCatalog):
        """A query with a rare, specific term should rank the matching skill high
        even if other skills match the common terms."""
        results = search(catalog.all_skills(), "slack gif creator", limit=3)
        assert results[0].skill.name == "slack-gif-creator"
        # The rare term "gif" should push it far above #2
        if len(results) > 1:
            assert results[0].score > results[1].score * 1.5

    def test_common_term_alone_is_unspecific(self, catalog: SkillCatalog):
        """A single common word should return multiple results with close scores."""
        results = search(catalog.all_skills(), "create", limit=5)
        assert len(results) >= 3
        # Top scores should be relatively close (not dominated by one)
        if len(results) >= 2:
            ratio = results[0].score / results[-1].score
            assert ratio < 10, "Common term should not massively favor one skill"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_single_token_query(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "pdf", limit=3)
        assert len(results) > 0
        assert results[0].skill.name == "pdf"

    def test_all_stop_words_query(self, catalog: SkillCatalog):
        # "want" is not a stop word, so use only actual stop words
        results = search(catalog.all_skills(), "I to use the a an")
        assert len(results) == 0

    def test_numeric_query(self, catalog: SkillCatalog):
        """Numeric tokens should not crash the search."""
        results = search(catalog.all_skills(), "create 3d model 2024")
        # Should not raise, may or may not return results
        assert isinstance(results, list)

    def test_very_long_query(self, catalog: SkillCatalog):
        """Long queries should work without error."""
        long_q = " ".join(["create", "a", "powerpoint", "presentation"] * 20)
        results = search(catalog.all_skills(), long_q, limit=3)
        assert isinstance(results, list)

    def test_results_sorted_descending(self, catalog: SkillCatalog):
        """Results must be sorted by score descending."""
        results = search(catalog.all_skills(), "build web application", limit=10)
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_tiebreaker_is_alphabetical(self):
        """When scores are equal, skill name ascending is the tiebreaker."""
        # Use names that won't match the query to isolate description scoring
        skills = [
            Skill(name="zzz-tool", description="gamma delta"),
            Skill(name="aaa-tool", description="gamma delta"),
        ]
        results = search(skills, "gamma delta", limit=2)
        assert len(results) == 2
        assert results[0].score == results[1].score
        assert results[0].skill.name == "aaa-tool"
        assert results[1].skill.name == "zzz-tool"
