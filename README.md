# Ephemeral Skills

MCP server for on-demand skill discovery. Skills live on the server, agents find and read them via MCP tools — no installation, always fresh.

## Quick Start

```bash
# Install
uv sync

# Run (HTTP)
uv run python -m ephemeral_skills.server --skills-dir /path/to/skills

# Run (stdio)
uv run python -m ephemeral_skills.server --skills-dir /path/to/skills --transport stdio
```

Server starts at `http://0.0.0.0:8080/mcp`.

## MCP Tools

**`search_skills(query, limit?)`** — Find skills by keyword. Returns names + descriptions.

**`read_skill(name, file?)`** — Read a skill's full instructions, or a specific supporting file.

## Tests

Three test levels, each measuring a different layer of the delivery pipeline:

### 1. Unit tests — search quality (no LLM needed)

```bash
uv run pytest tests/test_catalog.py tests/test_search.py -v
```

Does the search engine return the right skill for a query? 39 tests covering SKILL.md parsing, catalog loading, and search ranking.

### 2. Trigger tests — does the agent search? (requires LLM)

```bash
uv run pytest tests/e2e/test_trigger.py -v
```

38 test cases: 21 tasks that should trigger `search_skills`, 17 trivial tasks that should not. All cases run concurrently. No LLM-as-judge — assertions are purely deterministic.

### 3. E2E delivery tests — full pipeline (requires LLM)

```bash
uv run pytest tests/e2e/test_e2e.py -v
```

15 test cases validating the full loop: agent searches, finds the right skill, reads it. Assertions are tool-trace only — we test delivery, not how the model interprets the skill.

#### Setup

Create `tests/e2e/.env` with your credentials:

```bash
# For Claude API
ANTHROPIC_API_KEY=sk-ant-...
E2E_BACKEND=claude

# Or for Ollama (default)
E2E_BACKEND=ollama
E2E_MODEL=qwen2.5:7b
```

#### CLI runner

```bash
uv run python scripts/run_e2e.py
uv run python scripts/run_e2e.py --cases pdf-extract,slide-deck
uv run python scripts/run_e2e.py --backend claude --model claude-sonnet-4-6
uv run python scripts/run_e2e.py --concurrency 15
```

## E2E Configuration

| Env / Flag | Default | Description |
|-----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required when backend is `claude` |
| `E2E_BACKEND` / `--backend` | `ollama` | `ollama` or `claude` |
| `E2E_MODEL` / `--model` | `qwen2.5:7b` | Model name |
| `E2E_OLLAMA_URL` / `--ollama-url` | `http://localhost:11434` | Ollama base URL |
| `E2E_CONCURRENCY` / `--concurrency` | `10` | Max concurrent LLM calls |
| `E2E_CASES` / `--cases` | all | Comma-separated case IDs |

## Server Configuration

| Flag | Env | Default | Description |
|------|-----|---------|-------------|
| `--skills-dir` | `SKILLS_DIR` | `./skills` | Path to skills directory |
| `--port` | — | `8080` | Server port |
| `--host` | — | `0.0.0.0` | Bind address |
| `--transport` | — | `streamable-http` | `streamable-http`, `stdio`, or `sse` |

## Design

See [DESIGN.md](DESIGN.md) for full architecture details, including:
- [Architecture diagram and MCP tool specs](DESIGN.md#architecture)
- [Search algorithm and scoring weights](DESIGN.md#search-algorithm)
- [SKILL.md parsing (two-pass YAML)](DESIGN.md#skillmd-parsing)
- [Tool description design — triggering agent search behavior](DESIGN.md#tool-description-design)
- [Testing strategy — 3 levels, agent loop, LLM-as-judge](DESIGN.md#testing-strategy)
- [Open questions for v2](DESIGN.md#open-questions-out-of-scope-for-v1)
