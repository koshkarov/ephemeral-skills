"""Shared test fixtures."""

from pathlib import Path

import pytest

from ephemeral_skills.catalog import SkillCatalog

SKILLS_DIR = Path("/home/brealx/repos/skills/skills")


@pytest.fixture
def skills_dir() -> Path:
    """Path to the Anthropic shared skills directory."""
    if not SKILLS_DIR.is_dir():
        pytest.skip("Skills directory not found at %s" % SKILLS_DIR)
    return SKILLS_DIR


@pytest.fixture
def catalog(skills_dir: Path) -> SkillCatalog:
    """A loaded skill catalog from the shared skills."""
    cat = SkillCatalog()
    cat.load_directory(skills_dir)
    assert len(cat) > 0, "No skills loaded"
    return cat
