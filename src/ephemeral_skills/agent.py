"""Minimal agent loop for e2e testing.

Connects an LLM (Ollama or Claude) to the ephemeral-skills catalog via
tool calling. The agent receives a task, can call search_skills / read_skill,
and produces a final response.

This is NOT a production agent — it's a test harness that records tool call
traces for assertion-based grading.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .catalog import SkillCatalog, list_resources, read_resource
from .search import search

logger = logging.getLogger(__name__)

# Tool definitions in OpenAI-compatible format (used by Ollama)
TOOL_DEFINITIONS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "search_skills",
            "description": (
                "Search for skills matching a keyword query. "
                "Returns skill names and descriptions ranked by relevance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords describing what you need (e.g. 'create pdf', 'build mcp server')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Number of results to skip for pagination (default 0)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "List all available skills with pagination. "
                "Use this to browse the full catalog when you're unsure what to search for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "offset": {
                        "type": "integer",
                        "description": "Number of skills to skip for pagination (default 0)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per page (default 20)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": (
                "Read a skill's full instructions, or a specific supporting file. "
                "Call this after search_skills to load the skill you want to use. "
                "If multiple search results look relevant, read the most promising one first — then read others if you need more context or a better fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name (from search results)",
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional: relative path to a supporting file",
                    },
                },
                "required": ["name"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are a helpful assistant with access to a skills server. When a task \
requires specialized knowledge you don't have, search for a relevant skill \
using search_skills, then read it with read_skill and follow its instructions.

Do NOT search for skills when the task is trivial or you already know how to \
handle it (e.g., simple math, general knowledge questions).

When you find and read a skill, follow its instructions to help the user."""

MAX_TURNS = 10


@dataclass
class ToolCall:
    """A recorded tool call from the agent loop."""

    tool: str
    arguments: dict
    result: str


@dataclass
class AgentResult:
    """The outcome of running the agent loop on a task."""

    task: str
    response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    turns: int = 0
    error: str | None = None


def execute_tool(catalog: SkillCatalog, tool_name: str, arguments: dict) -> str:
    """Execute a tool call against the catalog. Returns the tool result as a string."""
    if tool_name == "search_skills":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        offset = arguments.get("offset", 0)
        all_results = search(catalog.all_skills(), query, limit=offset + limit)
        page = all_results[offset:]
        return json.dumps({
            "results": [
                {"name": r.skill.name, "description": r.skill.description, "relevance_score": round(r.score, 1)}
                for r in page
            ],
            "count": len(page),
            "totalCount": len(catalog),
            "query": query,
        })

    elif tool_name == "list_skills":
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 20)
        all_skills = sorted(catalog.all_skills(), key=lambda s: s.name)
        page = all_skills[offset:offset + limit]
        return json.dumps({
            "skills": [{"name": s.name, "description": s.description} for s in page],
            "count": len(page),
            "totalCount": len(catalog),
            "offset": offset,
        })

    elif tool_name == "read_skill":
        name = arguments.get("name", "")
        file_path = arguments.get("file")
        skill = catalog.get(name)
        if skill is None:
            return json.dumps({
                "error": f"Skill '{name}' not found",
                "available_skills": [s.name for s in catalog.all_skills()],
            })
        if file_path:
            content = read_resource(skill, file_path)
            if content is None:
                return json.dumps({
                    "error": f"File '{file_path}' not found in skill '{name}'",
                    "available_resources": list_resources(skill),
                })
            return json.dumps({"name": name, "file": file_path, "content": content})

        resources = list_resources(skill)
        return json.dumps({"name": name, "content": skill.body, "resources": resources})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


class LLMBackend(ABC):
    """Abstract backend for LLM API calls."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> dict:
        """Send messages to the LLM, return the response.

        Returns a dict with:
          - "content": str (text response, may be empty if tool calls)
          - "tool_calls": list[dict] (each with "name" and "arguments")
          - "stop": bool (True if the model is done, no more tool calls)
        """
        ...


class OllamaBackend(LLMBackend):
    """Ollama API backend (OpenAI-compatible tool calling)."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5:7b"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=120.0)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        resp = await self.client.post(f"{self.base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        message = data.get("message", {})
        content = message.get("content", "")
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            tool_calls.append({
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", {}),
            })

        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop": len(tool_calls) == 0,
            "_raw_message": message,
        }


class ClaudeBackend(LLMBackend):
    """Anthropic Claude API backend."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str | None = None):
        try:
            import anthropic
        except ImportError:
            raise ImportError("Install anthropic SDK: uv add anthropic")
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        # Convert OpenAI tool format to Claude format
        claude_tools = []
        if tools:
            for t in tools:
                fn = t["function"]
                claude_tools.append({
                    "name": fn["name"],
                    "description": fn["description"],
                    "input_schema": fn["parameters"],
                })

        # Separate system message from conversation
        system = ""
        conv_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            elif m["role"] == "tool":
                # Claude expects tool results as tool_result content blocks
                conv_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_use_id", m.get("_tool_use_id", "unknown")),
                        "content": m["content"],
                    }],
                })
            else:
                conv_messages.append(m)

        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": conv_messages,
        }
        if system:
            kwargs["system"] = system
        if claude_tools:
            kwargs["tools"] = claude_tools

        response = await self.client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "name": block.name,
                    "arguments": block.input,
                    "_tool_use_id": block.id,
                })

        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop": response.stop_reason == "end_turn",
            "_raw_response": response,
        }


async def run_agent(
    task: str,
    catalog: SkillCatalog,
    backend: LLMBackend,
    system_prompt: str = SYSTEM_PROMPT,
) -> AgentResult:
    """Run the agent loop: task → tool calls → final response.

    The agent can call search_skills and read_skill against the catalog.
    Returns the full trace of tool calls and the final response.
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    tool_calls_trace: list[ToolCall] = []
    turns = 0

    for _ in range(MAX_TURNS):
        turns += 1
        try:
            response = await backend.chat(messages, tools=TOOL_DEFINITIONS_OPENAI)
        except Exception as e:
            return AgentResult(
                task=task,
                response="",
                tool_calls=tool_calls_trace,
                turns=turns,
                error=f"LLM API error: {e}",
            )

        if response["stop"] or not response["tool_calls"]:
            return AgentResult(
                task=task,
                response=response["content"],
                tool_calls=tool_calls_trace,
                turns=turns,
            )

        # Process tool calls
        # Add assistant message with tool calls to history
        if isinstance(backend, ClaudeBackend):
            # Claude: reconstruct the assistant message with content blocks
            assistant_content = []
            if response["content"]:
                assistant_content.append({"type": "text", "text": response["content"]})
            for tc in response["tool_calls"]:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("_tool_use_id", "unknown"),
                    "name": tc["name"],
                    "input": tc["arguments"],
                })
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute each tool and add results
            for tc in response["tool_calls"]:
                result = execute_tool(catalog, tc["name"], tc["arguments"])
                tool_calls_trace.append(ToolCall(
                    tool=tc["name"],
                    arguments=tc["arguments"],
                    result=result,
                ))
                messages.append({
                    "role": "tool",
                    "tool_use_id": tc.get("_tool_use_id", "unknown"),
                    "_tool_use_id": tc.get("_tool_use_id", "unknown"),
                    "content": result,
                })
        else:
            # Ollama/OpenAI: add assistant message then tool results
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": [
                    {
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        }
                    }
                    for tc in response["tool_calls"]
                ],
            })
            for tc in response["tool_calls"]:
                result = execute_tool(catalog, tc["name"], tc["arguments"])
                tool_calls_trace.append(ToolCall(
                    tool=tc["name"],
                    arguments=tc["arguments"],
                    result=result,
                ))
                messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_name": tc["name"],
                })

    return AgentResult(
        task=task,
        response="",
        tool_calls=tool_calls_trace,
        turns=turns,
        error="Max turns exceeded",
    )
