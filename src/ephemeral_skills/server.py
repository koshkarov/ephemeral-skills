"""MCP server — tool definitions and handlers for ephemeral skill discovery."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .catalog import SkillCatalog, list_resources, read_resource
from .search import search

logger = logging.getLogger(__name__)


def _get_skills_dir() -> Path:
    """Resolve the skills directory from env or default."""
    env = os.environ.get("SKILLS_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd() / "skills"


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Load the skill catalog on startup."""
    catalog = SkillCatalog()
    skills_dir = _get_skills_dir()
    count = catalog.load_directory(skills_dir)
    logger.info("Ephemeral Skills server ready — %d skills from %s", count, skills_dir)
    yield {"catalog": catalog}


mcp = FastMCP(
    name="ephemeral-skills",
    instructions=(
        "This server provides a library of specialized skills. ALWAYS use search_skills "
        "before attempting any non-trivial task — skills contain expert procedures and "
        "specific tools that produce better results than general knowledge. "
        "Use search_skills first, then read_skill to load the full instructions."
    ),
    host="0.0.0.0",
    port=8080,
    lifespan=lifespan,
)


@mcp.tool()
async def search_skills(query: str, limit: int = 5) -> str:
    """MANDATORY first step before starting any user task. Search for a specialized skill that may contain expert procedures, project-specific standards, or required tools for the task. Call this even if you think you know how to do it — the skill may have specific requirements that override general knowledge. The only exceptions are single-turn factual questions (definitions, math, simple lookups). The search is fast; skipping it risks producing work that doesn't follow required procedures.

    Results are ranked by relevance. When multiple results look relevant, read the descriptions carefully and pick the best fit — or read more than one skill if the task spans multiple domains.

    Args:
        query: Keywords describing the task (e.g. "create pdf", "slide deck", "test web app", "code review", "write status update")
        limit: Maximum number of results to return (default 5)
    """
    catalog: SkillCatalog = mcp.get_context().request_context.lifespan_context["catalog"]
    results = search(catalog.all_skills(), query, limit=limit)
    return json.dumps({
        "results": [
            {
                "name": r.skill.name,
                "description": r.skill.description,
                "relevance_score": round(r.score, 1),
            }
            for r in results
        ],
        "total_available": len(catalog),
        "query": query,
    })


@mcp.tool()
async def read_skill(name: str, file: str | None = None) -> str:
    """Read a skill's full instructions or a specific supporting file. Always read at least one skill before starting the task — it contains the exact tools, code patterns, and procedures to follow. If multiple search results look relevant, read the most promising one first, then read others if you need more context or a better fit.

    Args:
        name: Skill name (from search results)
        file: Optional relative path to a supporting file (e.g. "references/api.md")
    """
    catalog: SkillCatalog = mcp.get_context().request_context.lifespan_context["catalog"]
    skill = catalog.get(name)
    if skill is None:
        available = [s.name for s in catalog.all_skills()]
        return json.dumps({"error": f"Skill '{name}' not found", "available_skills": available})

    if file:
        content = read_resource(skill, file)
        if content is None:
            resources = list_resources(skill)
            return json.dumps({
                "error": f"File '{file}' not found in skill '{name}'",
                "available_resources": resources,
            })
        return json.dumps({
            "name": name,
            "file": file,
            "content": content,
        })

    resources = list_resources(skill)
    return json.dumps({
        "name": name,
        "content": skill.body,
        "resources": resources,
    })


def create_server(
    skills_dir: str | Path | None = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> FastMCP:
    """Create a configured MCP server instance. Useful for testing."""
    if skills_dir:
        os.environ["SKILLS_DIR"] = str(skills_dir)
    server = FastMCP(
        name="ephemeral-skills",
        instructions=(
            "This server provides on-demand skill discovery. "
            "Use search_skills to find relevant skills by keyword, "
            "then read_skill to load the full instructions."
        ),
        host=host,
        port=port,
        lifespan=lifespan,
    )
    # Register the same tools on the new instance
    server.tool()(search_skills)
    server.tool()(read_skill)
    return server


def main():
    """Entry point — run the MCP server over streamable HTTP."""
    import argparse

    parser = argparse.ArgumentParser(description="Ephemeral Skills MCP Server")
    parser.add_argument("--skills-dir", type=str, help="Path to skills directory")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "sse"],
        default="streamable-http",
        help="MCP transport (default: streamable-http)",
    )
    args = parser.parse_args()

    if args.skills_dir:
        os.environ["SKILLS_DIR"] = args.skills_dir

    mcp.settings.host = args.host
    mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
