# Ephemeral Skills — Design Document

## Problem

Skills today live on the local filesystem. This requires:
- **Installation**: copying skill directories to `~/.agents/skills/` or project-level paths
- **Maintenance**: manually updating skills when upstream changes
- **Distribution**: no standard way to share skills across a team or company

The progressive disclosure pattern of skills (catalog → instructions → resources) naturally maps to a request/response model — like browsing web pages. What if skills lived on a server and clients discovered them on demand, without ever installing them locally?

## Concept: Ephemeral Skills

Skills stored on a server, discovered via search, read via MCP tools, never persisted on the client. The word "ephemeral" means: **fetched on demand, used, then forgotten**.

```
Agent gets a task it doesn't know how to do
  → search_skills("how to do X")
  → gets ranked matches with descriptions
  → reads the most relevant skill
  → follows the instructions
  → skill content is not persisted
```

### Key Properties

- **Zero install** — connect to the MCP server, all skills are available
- **Always fresh** — no version sync, no stale local copies
- **Agent-agnostic** — any agent that speaks MCP can use them
- **Progressive disclosure preserved** — search returns Tier 1 (metadata), read returns Tier 2 (instructions), read with file path returns Tier 3 (resources)

## Architecture

```
┌─────────────┐       MCP (Streamable HTTP)      ┌──────────────────┐
│   AI Agent   │ ◄─────────────────────────────► │  Ephemeral Skills │
│  (any client)│    search_skills / read_skill    │    MCP Server     │
└─────────────┘                                   └────────┬─────────┘
                                                           │
                                                           │ reads
                                                           ▼
                                                  ┌──────────────────┐
                                                  │  Skills Directory │
                                                  │   (local folder)  │
                                                  │                   │
                                                  │ skill-a/SKILL.md  │
                                                  │ skill-b/SKILL.md  │
                                                  │ ...               │
                                                  └──────────────────┘
```

The server is a Python MCP server over **Streamable HTTP** (deployable to any host):
1. On startup: scans a directory of skills, parses all `SKILL.md` frontmatters into an in-memory catalog
2. Exposes two MCP tools for agents to discover and read skills
3. Default endpoint: `http://<host>:8080/mcp`

The skills directory follows the [Agent Skills specification](https://github.com/anthropics/agentskills). Each skill is a directory containing a `SKILL.md` file with YAML frontmatter + markdown body, plus optional `scripts/`, `references/`, and `assets/` directories.

## MCP Tools

### `search_skills`

Find skills matching a query. Returns Tier 1 metadata only.

**Parameters:**
| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| `query` | string | yes | — | Search query — keywords describing what the agent needs |
| `limit` | int | no | 5 | Maximum number of results to return |

**Returns:** JSON with ranked results:
```json
{
  "results": [
    {
      "name": "pdf",
      "description": "Use this skill whenever the user wants to do anything with PDF files..."
    }
  ],
  "total_available": 17,
  "query": "extract text from pdf"
}
```

**Error handling:** Returns an empty results list for queries that match nothing. Never errors on valid input.

### Tool Description Design

The `search_skills` tool description is critical — it determines whether the agent actually calls the tool before starting a task. The description must be assertive enough to trigger search for non-obvious tasks (e.g., "write a status update") while allowing the agent to skip search for trivial Q&A.

Current approach: frame the tool as a **mandatory first step** rather than an optional lookup:
- "MANDATORY first step before starting any user task"
- "Call this even if you think you know how to do it"
- "The only exceptions are single-turn factual questions"

This is the single most impactful lever for the system's effectiveness. If the agent doesn't search, it can't find skills.

### `read_skill`

Load a skill's full content or a specific supporting file. Returns Tier 2 or Tier 3 content.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | yes | Skill name (from search results) |
| `file` | string | no | Relative path to a supporting file (e.g., `references/api.md`) |

**Returns (no `file`):** Full skill instructions (frontmatter stripped) + list of available resources:
```json
{
  "name": "mcp-builder",
  "content": "# MCP Server Development Guide\n\n...",
  "resources": [
    "references/fastmcp.md",
    "references/typescript-sdk.md",
    "scripts/scaffold.py"
  ]
}
```

**Returns (with `file`):** Content of the specific file:
```json
{
  "name": "pdf",
  "file": "references/REFERENCE.md",
  "content": "# PDF Reference\n\n..."
}
```

**Error handling:**
- Skill not found → returns `{"error": "...", "available_skills": [...]}`
- File not found → returns `{"error": "...", "available_resources": [...]}`
- Both cases include helpful context so the agent can self-correct.

**Security:** Path traversal prevention — the resolved path must stay within the skill's directory.

## Skill Format

Follows the [Agent Skills specification](https://github.com/anthropics/agentskills/blob/main/docs/specification.mdx):

```yaml
---
name: pdf-processing
description: >
  Extract PDF text, fill forms, merge files. Use when handling PDFs
  or when the user mentions document extraction.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
  tags: "pdf document extraction forms"
---

# PDF Processing

## When to use
...
```

### Key conventions from the spec:
- `name`: 1-64 chars, lowercase kebab-case, must match directory name
- `description`: max 1024 chars, describes what + when to use (this is what search indexes)
- Body: markdown, recommended <500 lines / <5000 tokens
- Supporting files in `scripts/`, `references/`, `assets/`
- Progressive disclosure: keep SKILL.md focused, defer heavy content to separate files

### Tags for search

We extend `metadata` with a `tags` field (space-separated keywords) to improve search beyond what `name` and `description` provide:

```yaml
metadata:
  tags: "pdf document extraction ocr forms merge"
```

This is compatible with the spec (`metadata` accepts arbitrary key-value string pairs).

## Search Algorithm

Keyword matching against skill `name`, `description`, and `metadata.tags`.

### Tokenization

Text is split into lowercase alphanumeric tokens with these filters:
- **Stop words removed**: common English words ("the", "a", "is", "use", "using", etc.)
- **Minimum length 2**: single-character tokens produce too many false substring matches

### Scoring

Each query token is scored against each field's token set:

| Match type | Name | Description | Tags |
|-----------|------|-------------|------|
| Exact token match | 10.0 | 3.0 | 4.0 |
| Substring match | 5.0 | 1.5 | 2.0 |

Substring matching requires **both tokens to be ≥ 3 characters** to avoid false positives (e.g., "go" matching "algorithm"). A substring match means one token contains the other (e.g., "presentation" matches "presentations").

Scores accumulate across all query tokens. Results are sorted by total score descending.

### Design rationale

The search is intentionally simple. The agent calling the tool is already an LLM — it can formulate good keyword queries and retry with different terms. If keyword search proves insufficient at scale, the MCP tool interface stays the same and only the backend changes (e.g., to BM25, embeddings, or LLM-as-judge).

## SKILL.md Parsing

The parser follows Claude Code's two-pass approach (ported from `code/src/utils/frontmatterParser.ts`):

1. **Regex extraction**: `^---\s*\n([\s\S]*?)---\s*\n?` captures the YAML block
2. **First pass**: `yaml.safe_load` on the raw text
3. **Second pass (on failure)**: auto-quote values containing YAML special characters (`{}[]&#!|>%@` and `: `), then retry `yaml.safe_load`
4. **Field extraction**: `name` and `description` are required; `license`, `compatibility`, `metadata` are optional
5. **Body**: everything after the closing `---`, stripped of leading/trailing whitespace

The two-pass approach handles real-world skills that use unquoted special characters in descriptions.

### Differences from Claude Code's parser
- Claude Code uses TypeScript + `js-yaml`; we use Python + `pyyaml`
- Claude Code has additional frontmatter fields specific to its runtime (`hooks`, `context`, `agent`, `paths`, `shell`, `effort`, `model`). We only extract spec-standard fields.
- Claude Code does runtime variable substitution (`${CLAUDE_SKILL_DIR}`, `${CLAUDE_SESSION_ID}`). We don't — ephemeral skills are read-only.

## Testing Strategy

Testing validates the entire concept: can an agent discover, read, and effectively use ephemeral skills via MCP?

### Level 1: Unit Tests — Catalog & Search Quality

**39 tests, all passing.**

- **Catalog tests** (`test_catalog.py`, 17 tests): frontmatter parsing, skill parsing, catalog loading, resource listing, path traversal prevention
- **Search tests** (`test_search.py`, 22 tests): tokenizer, 15 parametrized search quality cases against real skills, edge cases

These run fast (<0.5s), no LLM needed, pure Python pytest.

### Level 2: Integration Tests — MCP Round-Trip

Test the MCP server tool calls end-to-end via HTTP (`test_server.py`). Requires a running server instance.

### Level 3: End-to-End Tests — Agent Loop

The real test: can an actual LLM agent use the MCP server to find and apply the right skill?

**15 test cases** with natural, non-obvious prompts — none mention "skills" or "search". The agent must figure out on its own that a skill exists.

**Architecture:**

```
Task → Agent Loop → Tool Calls → Grading
         │                          │
         │  LLM (Ollama/Claude)     │  Tool trace (deterministic)
         │  ↕ tool calls            │  Output quality (LLM-as-judge)
         │  Catalog (search/read)   │
         ↓                          ↓
    AgentResult              GradingResult
```

**How it works:**
1. Load the skill catalog in-process (no separate server needed)
2. Send the task to an LLM (Ollama or Claude API) with tool definitions
3. The agent loop executes tool calls (`search_skills`, `read_skill`) against the catalog
4. Capture the full tool call trace and final response
5. Grade with two assertion types:
   - **Tool trace** (deterministic): did it call `search_skills`? Did it call `read_skill` with the expected skill name?
   - **Output quality** (LLM-as-judge): does the response reference the right libraries/tools? Does it follow the skill's guidance?

**Test cases** are defined in `tests/e2e/test_cases.json` — add, modify, or remove cases without touching code.

**Backends:** Ollama (local, free, default) and Claude API. The judge backend can be configured separately from the agent backend.

**Configuration** via `tests/e2e/.env` (gitignored), env vars, or CLI flags. See README for details.

## Project Structure

```
ephemeral-skills/
├── DESIGN.md                         # This document
├── README.md                         # Quick start and test instructions
├── pyproject.toml                    # Python project config (uv)
├── .mcp.json                         # MCP server config for Claude Code
├── src/
│   └── ephemeral_skills/
│       ├── __init__.py
│       ├── server.py                 # MCP server — tool definitions + handlers
│       ├── catalog.py                # Skill discovery — scan dir, parse SKILL.md
│       ├── search.py                 # Keyword search engine
│       ├── agent.py                  # Agent loop for e2e testing (Ollama/Claude)
│       └── grader.py                 # Grading — tool trace + LLM-as-judge
├── tests/
│   ├── conftest.py                   # Shared fixtures (catalog from real skills)
│   ├── test_catalog.py               # Level 1: parsing + catalog (17 tests)
│   ├── test_search.py                # Level 1: search quality (22 tests)
│   ├── test_server.py                # Level 2: MCP round-trip
│   └── e2e/
│       ├── .env                      # Credentials (gitignored)
│       ├── test_cases.json           # 15 test cases — edit here, not in code
│       └── test_e2e.py               # Level 3: agent loop (parametrized from JSON)
└── scripts/
    └── run_e2e.py                    # CLI runner for e2e tests
```

## Key Implementation Details

- **Lifespan pattern**: The skill catalog is loaded once at startup via FastMCP's `lifespan` async context manager. Tool handlers access it through `mcp.get_context().request_context.lifespan_context["catalog"]`.
- **`create_server()` factory**: Exported from `server.py` for programmatic use — creates a configured FastMCP instance without touching the module-level singleton.
- **Skills dir resolution**: `SKILLS_DIR` env var → `--skills-dir` CLI arg → `./skills` fallback.
- **Wrong path detection**: The catalog warns when it finds subdirectories with nested skills but no SKILL.md at their root (e.g., pointing at `/repos/skills/` instead of `/repos/skills/skills/`).
- **Transport options**: `--transport streamable-http` (default, for deployment), `stdio` (for local MCP pipes), `sse` (legacy).
- **`.env` loading**: Both `test_e2e.py` and `run_e2e.py` auto-load `tests/e2e/.env` via `python-dotenv`.

## Open Questions (Out of Scope for v1)

- **Publishing workflow**: How do skills get into the server's directory? (Manual for now)
- **Authentication**: Who can search/read skills? (No auth for v1 — local use only)
- **Catalog in system prompt**: Should the server support a `list_all_skills` tool for agents that want to build a catalog upfront? (Not needed if search works well)
- **Caching/TTL**: Should the server watch for filesystem changes? (Restart to reload for v1)
- **Multi-server federation**: Multiple ephemeral skill servers with merged results? (Future)
- **Remote script execution**: Skills can reference `scripts/` files, but the agent can't execute them remotely. A future version could expose a `run_skill_script` tool.
