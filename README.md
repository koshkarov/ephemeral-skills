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

```bash
# Unit + search quality (39 tests, no LLM needed)
uv run pytest tests/test_catalog.py tests/test_search.py -v

# E2E with Ollama (requires running Ollama with a model)
uv run pytest tests/e2e/test_e2e.py -v

# E2E with Claude API
E2E_BACKEND=claude uv run pytest tests/e2e/test_e2e.py -v

# E2E CLI runner (standalone, with JSON output)
uv run python scripts/run_e2e.py --skills-dir /path/to/skills --output results.json

# Run specific e2e cases
uv run python scripts/run_e2e.py --cases pdf-extract,mcp-server-build

# Skip LLM-as-judge (only check tool call traces)
uv run python scripts/run_e2e.py --skip-judge
```

## E2E Configuration

| Env / Flag | Default | Description |
|-----------|---------|-------------|
| `E2E_BACKEND` / `--backend` | `ollama` | `ollama` or `claude` |
| `E2E_MODEL` / `--model` | `qwen2.5:7b` | Model name |
| `E2E_OLLAMA_URL` / `--ollama-url` | `http://localhost:11434` | Ollama base URL |
| `E2E_JUDGE_BACKEND` / `--judge-backend` | same as backend | Separate model for grading |
| `E2E_SKIP_JUDGE` / `--skip-judge` | `false` | Skip output quality grading |
| `E2E_CASES` / `--cases` | all | Comma-separated case IDs |

## Server Configuration

| Flag | Env | Default | Description |
|------|-----|---------|-------------|
| `--skills-dir` | `SKILLS_DIR` | `./skills` | Path to skills directory |
| `--port` | — | `8080` | Server port |
| `--host` | — | `0.0.0.0` | Bind address |
| `--transport` | — | `streamable-http` | `streamable-http`, `stdio`, or `sse` |

See [DESIGN.md](DESIGN.md) for architecture details.
