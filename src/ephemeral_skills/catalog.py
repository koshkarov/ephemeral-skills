"""Skill discovery — scan directory, parse SKILL.md files, build in-memory catalog."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")

# Characters that break YAML parsing when unquoted (adapted from Claude Code's
# frontmatterParser.ts). We auto-quote values containing these on retry.
YAML_SPECIAL_RE = re.compile(r"[{}\[\]*&#!|>%@`]|: ")


@dataclass
class Skill:
    """A parsed skill from a SKILL.md file."""

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    body: str = ""
    directory: Path = field(default_factory=lambda: Path("."))

    @property
    def tags(self) -> str:
        """Space-separated tags from metadata, if present."""
        return self.metadata.get("tags", "")


def _quote_problematic_values(text: str) -> str:
    """Auto-quote YAML values that contain special characters.

    Two-pass approach from Claude Code: try raw YAML first, then retry with
    values wrapped in double quotes so that braces, colons-with-space, etc.
    don't break the parser.
    """
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        m = re.match(r"^([a-zA-Z_-]+):\s+(.+)$", line)
        if m:
            key, value = m.group(1), m.group(2)
            # Skip already-quoted values
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                result.append(line)
                continue
            if YAML_SPECIAL_RE.search(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(f'{key}: "{escaped}"')
                continue
        result.append(line)
    return "\n".join(result)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and markdown body from a SKILL.md file.

    Returns (frontmatter_dict, body_text). On parse failure returns ({}, full content).
    """
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body = content[match.end() :]

    # First pass: try raw YAML
    try:
        parsed = yaml.safe_load(yaml_text)
        if isinstance(parsed, dict):
            return parsed, body
    except yaml.YAMLError:
        pass

    # Second pass: auto-quote problematic values
    try:
        quoted = _quote_problematic_values(yaml_text)
        parsed = yaml.safe_load(quoted)
        if isinstance(parsed, dict):
            return parsed, body
    except yaml.YAMLError as e:
        logger.warning("Failed to parse YAML frontmatter: %s", e)

    return {}, content


def parse_skill(skill_dir: Path) -> Skill | None:
    """Parse a SKILL.md file from a directory. Returns None on failure."""
    # Prefer SKILL.md, fall back to skill.md
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        skill_file = skill_dir / "skill.md"
        if not skill_file.is_file():
            return None

    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read %s: %s", skill_file, e)
        return None

    frontmatter, body = parse_frontmatter(content)

    name = frontmatter.get("name")
    description = frontmatter.get("description")

    if not name or not isinstance(name, str):
        logger.warning("Skill at %s missing 'name' field, skipping", skill_dir)
        return None
    if not description or not isinstance(description, str):
        logger.warning("Skill at %s missing 'description' field, skipping", skill_dir)
        return None

    # Coerce metadata values to strings
    raw_metadata = frontmatter.get("metadata") or {}
    metadata = {str(k): str(v) for k, v in raw_metadata.items()} if isinstance(raw_metadata, dict) else {}

    return Skill(
        name=name.strip(),
        description=description.strip(),
        license=frontmatter.get("license"),
        compatibility=frontmatter.get("compatibility"),
        metadata=metadata,
        body=body.strip(),
        directory=skill_dir,
    )


def list_resources(skill: Skill) -> list[str]:
    """List supporting files in a skill directory (scripts/, references/, assets/, etc.).

    Returns relative paths from the skill root.
    """
    resources: list[str] = []
    for root, _dirs, files in os.walk(skill.directory):
        root_path = Path(root)
        for f in files:
            rel = (root_path / f).relative_to(skill.directory).as_posix()
            if rel == "SKILL.md" or rel == "skill.md":
                continue
            resources.append(rel)
    resources.sort()
    return resources


def read_resource(skill: Skill, file_path: str) -> str | None:
    """Read a supporting file from within a skill directory.

    Returns None if the file doesn't exist or the path escapes the skill dir.
    """
    resolved = (skill.directory / file_path).resolve()
    # Path traversal prevention
    if not str(resolved).startswith(str(skill.directory.resolve())):
        return None
    if not resolved.is_file():
        return None
    try:
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


class SkillCatalog:
    """In-memory catalog of all discovered skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    @property
    def skills(self) -> dict[str, Skill]:
        return self._skills

    def load_directory(self, skills_dir: Path) -> int:
        """Scan a directory for skill subdirectories containing SKILL.md.

        Returns the number of skills loaded.
        """
        if not skills_dir.is_dir():
            logger.warning("Skills directory does not exist: %s", skills_dir)
            return 0

        count = 0
        dirs_scanned = 0
        dirs_without_skill = []
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            dirs_scanned += 1
            skill = parse_skill(entry)
            if skill is None:
                # Check if this dir has subdirs that themselves contain SKILL.md
                # (common mistake: pointing at parent instead of skills/ dir)
                nested = list(entry.glob("*/SKILL.md"))
                if nested:
                    dirs_without_skill.append((entry.name, len(nested)))
                continue
            if skill.name in self._skills:
                logger.info("Skill '%s' already loaded, skipping duplicate from %s", skill.name, entry)
                continue
            self._skills[skill.name] = skill
            count += 1

        logger.info("Loaded %d skills from %s", count, skills_dir)

        if dirs_without_skill:
            hints = ", ".join(
                f"'{name}/' ({n} skills inside)" for name, n in dirs_without_skill
            )
            logger.warning(
                "Found subdirectories with nested skills but no SKILL.md at their root: %s. "
                "Did you mean to point --skills-dir at one of these?",
                hints,
            )

        if count == 0 and dirs_scanned > 0:
            logger.warning(
                "No skills found in %s (%d subdirectories scanned). "
                "Each skill must be a directory containing a SKILL.md file.",
                skills_dir,
                dirs_scanned,
            )

        return count

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)
