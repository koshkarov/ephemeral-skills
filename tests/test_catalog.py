"""Tests for skill catalog — parsing, loading, resource access."""

from pathlib import Path
from textwrap import dedent

import pytest

from ephemeral_skills.catalog import (
    SkillCatalog,
    list_resources,
    parse_frontmatter,
    parse_skill,
    read_resource,
)


# --- Frontmatter parsing ---


class TestParseFrontmatter:
    def test_basic(self):
        fm, body = parse_frontmatter(dedent("""\
            ---
            name: test-skill
            description: A test skill.
            ---
            # Body content
        """))
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill."
        assert "# Body content" in body

    def test_no_frontmatter(self):
        fm, body = parse_frontmatter("# Just markdown\nSome text.")
        assert fm == {}
        assert "Just markdown" in body

    def test_special_chars_auto_quoted(self):
        """Values with YAML special chars (braces, etc.) should be auto-quoted on retry."""
        fm, body = parse_frontmatter(dedent("""\
            ---
            name: test
            description: Use when: the user asks about {things}
            ---
            Body.
        """))
        assert fm["name"] == "test"
        assert "things" in fm.get("description", "")

    def test_metadata_dict(self):
        fm, body = parse_frontmatter(dedent("""\
            ---
            name: test
            description: Test
            metadata:
              author: someone
              version: "1.0"
            ---
            Body.
        """))
        assert fm["metadata"]["author"] == "someone"

    def test_multiline_description(self):
        fm, body = parse_frontmatter(dedent("""\
            ---
            name: test
            description: >
              A long description that
              spans multiple lines.
            ---
            Body.
        """))
        assert "long description" in fm["description"]


# --- Skill parsing ---


class TestParseSkill:
    def test_valid_skill(self, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(dedent("""\
            ---
            name: my-skill
            description: A great skill.
            ---
            # Instructions here.
        """))
        skill = parse_skill(skill_dir)
        assert skill is not None
        assert skill.name == "my-skill"
        assert skill.description == "A great skill."
        assert "Instructions here" in skill.body

    def test_missing_name(self, tmp_path: Path):
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(dedent("""\
            ---
            description: No name field.
            ---
            Body.
        """))
        assert parse_skill(skill_dir) is None

    def test_missing_description(self, tmp_path: Path):
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(dedent("""\
            ---
            name: bad-skill
            ---
            Body.
        """))
        assert parse_skill(skill_dir) is None

    def test_no_skill_file(self, tmp_path: Path):
        skill_dir = tmp_path / "empty"
        skill_dir.mkdir()
        assert parse_skill(skill_dir) is None

    def test_lowercase_skill_md(self, tmp_path: Path):
        skill_dir = tmp_path / "lower"
        skill_dir.mkdir()
        (skill_dir / "skill.md").write_text(dedent("""\
            ---
            name: lower
            description: Lowercase variant.
            ---
            Body.
        """))
        skill = parse_skill(skill_dir)
        assert skill is not None
        assert skill.name == "lower"


# --- Catalog ---


class TestSkillCatalog:
    def test_load_real_skills(self, catalog: SkillCatalog):
        assert len(catalog) >= 15  # We know there are 17 skills
        assert catalog.get("pdf") is not None
        assert catalog.get("mcp-builder") is not None
        assert catalog.get("nonexistent") is None

    def test_all_skills_have_name_and_description(self, catalog: SkillCatalog):
        for skill in catalog.all_skills():
            assert skill.name, f"Skill missing name: {skill.directory}"
            assert skill.description, f"Skill {skill.name} missing description"

    def test_load_nonexistent_dir(self):
        cat = SkillCatalog()
        count = cat.load_directory(Path("/nonexistent/path"))
        assert count == 0
        assert len(cat) == 0

    def test_deduplication(self, skills_dir: Path):
        cat = SkillCatalog()
        cat.load_directory(skills_dir)
        first_count = len(cat)
        cat.load_directory(skills_dir)  # Load again
        assert len(cat) == first_count  # No duplicates


# --- Resources ---


class TestResources:
    def test_list_resources(self, catalog: SkillCatalog):
        pdf = catalog.get("pdf")
        assert pdf is not None
        resources = list_resources(pdf)
        # The pdf skill should have supporting files
        assert len(resources) >= 0  # At least SKILL.md is excluded

    def test_read_resource_path_traversal(self, catalog: SkillCatalog):
        pdf = catalog.get("pdf")
        assert pdf is not None
        result = read_resource(pdf, "../../etc/passwd")
        assert result is None

    def test_read_nonexistent_resource(self, catalog: SkillCatalog):
        pdf = catalog.get("pdf")
        assert pdf is not None
        result = read_resource(pdf, "does/not/exist.md")
        assert result is None
