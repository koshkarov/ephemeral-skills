"""Level 1: Search quality tests — does the right skill rank first?"""

import pytest

from ephemeral_skills.catalog import SkillCatalog
from ephemeral_skills.search import search, tokenize


# --- Tokenizer ---


class TestTokenize:
    def test_basic(self):
        assert tokenize("create a PDF document") == ["create", "pdf", "document"]

    def test_stop_words_removed(self):
        tokens = tokenize("I want to use the skill for my project")
        assert "i" not in tokens
        assert "want" in tokens
        assert "skill" in tokens
        assert "project" in tokens

    def test_hyphenated_input(self):
        assert tokenize("mcp-builder") == ["mcp", "builder"]

    def test_empty(self):
        assert tokenize("") == []
        assert tokenize("the a an") == []


# --- Search quality against real skills ---

# Each test case: (query, expected_top_skill, should_not_be_first)
SEARCH_CASES = [
    # Clear matches — the query directly names the domain
    ("extract text from a pdf", "pdf", []),
    ("build an mcp server", "mcp-builder", []),
    ("create a word document", "docx", []),
    ("create a powerpoint presentation", "pptx", []),
    ("spreadsheet excel data analysis", "xlsx", []),
    ("build an app with claude api", "claude-api", []),
    ("test my web application", "webapp-testing", []),
    ("create a gif for slack", "slack-gif-creator", []),

    # Indirect matches — the query describes the need without naming the tool
    ("make slides for my pitch deck", "pptx", []),
    ("fill out a form in a pdf file", "pdf", []),
    ("I need to merge multiple pdf files into one", "pdf", []),
    ("write a status report for leadership", "internal-comms", []),
    ("style my artifact with company colors", "brand-guidelines", []),

    # Near-miss negative cases — should match a specific skill, not a close neighbor
    ("create generative art with code", "algorithmic-art", ["frontend-design"]),
    ("build a react landing page", "frontend-design", ["algorithmic-art"]),
]


class TestSearchQuality:
    @pytest.mark.parametrize("query,expected_top,should_not_be_first", SEARCH_CASES)
    def test_top_result(
        self,
        catalog: SkillCatalog,
        query: str,
        expected_top: str,
        should_not_be_first: list[str],
    ):
        results = search(catalog.all_skills(), query, limit=5)
        assert len(results) > 0, f"No results for query: {query}"

        top_name = results[0].skill.name
        assert top_name == expected_top, (
            f"Query '{query}': expected '{expected_top}' as top result, "
            f"got '{top_name}' (score={results[0].score}). "
            f"Top 3: {[(r.skill.name, r.score) for r in results[:3]]}"
        )

        for bad in should_not_be_first:
            assert top_name != bad, (
                f"Query '{query}': '{bad}' should not rank first"
            )

    def test_no_results_for_irrelevant_query(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "quantum physics thermodynamics")
        assert len(results) == 0 or results[0].score < 3.0

    def test_limit_respected(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "create", limit=3)
        assert len(results) <= 3

    def test_empty_query(self, catalog: SkillCatalog):
        results = search(catalog.all_skills(), "")
        assert len(results) == 0
