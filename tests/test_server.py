"""Level 2: MCP server integration tests — round-trip tool calls."""

import json

import pytest

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVER_URL = "http://127.0.0.1:8080/mcp"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
class TestMCPServer:
    """Tests that require a running MCP server.

    Start the server before running these:
        uv run python -m ephemeral_skills.server \\
            --skills-dir /path/to/skills/skills \\
            --port 8080
    """

    async def _get_session(self):
        """Connect to the running MCP server."""
        async with streamablehttp_client(SERVER_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    @pytest.mark.skipif(True, reason="Requires running server — run manually with --run-server")
    async def test_search_skills_returns_results(self):
        async for session in self._get_session():
            result = await session.call_tool("search_skills", {"query": "pdf"})
            data = json.loads(result.content[0].text)
            assert "results" in data
            assert len(data["results"]) > 0
            assert data["results"][0]["name"] == "pdf"

    @pytest.mark.skipif(True, reason="Requires running server — run manually with --run-server")
    async def test_read_skill_returns_content(self):
        async for session in self._get_session():
            result = await session.call_tool("read_skill", {"name": "pdf"})
            data = json.loads(result.content[0].text)
            assert data["name"] == "pdf"
            assert "content" in data
            assert len(data["content"]) > 100

    @pytest.mark.skipif(True, reason="Requires running server — run manually with --run-server")
    async def test_read_skill_not_found(self):
        async for session in self._get_session():
            result = await session.call_tool("read_skill", {"name": "nonexistent-skill"})
            data = json.loads(result.content[0].text)
            assert "error" in data
            assert "available_skills" in data

    @pytest.mark.skipif(True, reason="Requires running server — run manually with --run-server")
    async def test_read_skill_path_traversal_blocked(self):
        async for session in self._get_session():
            result = await session.call_tool(
                "read_skill", {"name": "pdf", "file": "../../etc/passwd"}
            )
            data = json.loads(result.content[0].text)
            assert "error" in data
