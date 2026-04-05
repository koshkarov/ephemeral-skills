"""Trigger tests — does the agent search when it should, and skip when it shouldn't?

These tests isolate the hardest problem: whether the LLM decides to call
search_skills based on the task. No LLM-as-judge needed — assertions are
purely deterministic (did search_skills appear in the tool trace?).

All cases within a group run concurrently via asyncio.gather for speed.

Run:
    uv run pytest tests/e2e/test_trigger.py -v
    uv run pytest tests/e2e/test_trigger.py -v -k "should_search"
    uv run pytest tests/e2e/test_trigger.py -v -k "should_not_search"
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

_env_file = Path(__file__).parent / ".env"
if _env_file.is_file():
    from dotenv import load_dotenv

    load_dotenv(_env_file)

import pytest

from ephemeral_skills.agent import AgentResult, ClaudeBackend, LLMBackend, OllamaBackend, run_agent
from ephemeral_skills.catalog import SkillCatalog

SKILLS_DIR = Path("/home/brealx/repos/skills/skills")
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"

# Max concurrent LLM calls (avoid rate limits)
MAX_CONCURRENCY = int(os.environ.get("E2E_CONCURRENCY", "10"))

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

# Tasks where the agent SHOULD call search_skills.
# These are intentionally natural — no mention of "skills" or "search".
SHOULD_SEARCH: list[tuple[str, str]] = [
    # Document creation
    ("doc-pdf-create", "Generate a PDF report from this quarterly data with charts and a cover page."),
    ("doc-word-letter", "Write a formal offer letter in Word format for a new hire starting next month."),
    ("doc-slides-investor", "Put together an investor pitch deck — 12 slides, we're raising Series B."),
    ("doc-spreadsheet-budget", "Build me a budget spreadsheet with formulas and a pivot summary."),

    # Creative / media
    ("creative-gif", "Make an animated GIF of a bouncing logo for our Slack workspace."),
    ("creative-art", "Generate some procedural art — I'm thinking fractals or Perlin noise."),
    ("creative-theme", "Design a dark-mode color theme for our internal dashboard."),

    # Technical / specialized
    ("tech-mcp", "I need Claude to be able to query our Postgres database directly."),
    ("tech-claude-api", "Show me how to call the Claude API from TypeScript with streaming."),
    ("tech-webapp-test", "Our checkout flow is broken on mobile — can you test it in a browser?"),
    ("tech-frontend", "Redesign the settings page — it's React with Tailwind, needs to feel more modern."),

    # Communication / writing
    ("comms-status", "Draft a weekly status update for the VP of Engineering."),
    ("comms-newsletter", "Write the monthly company newsletter — we launched two features and hired 5 people."),

    # Ambiguous but should lean toward search
    ("ambiguous-brand", "Make sure our app follows the brand guidelines when rendering reports."),
    ("ambiguous-canvas", "I need to generate certificate images with custom text overlaid."),
    ("ambiguous-doc-coauthor", "Help me co-author a technical design doc with proper structure."),

    # Indirect phrasing — describes the outcome, not the tool
    ("indirect-email-board", "I need something professional I can email to the board with our financials."),
    ("indirect-automate-api", "I want an assistant that can look up order status from our REST API."),
    ("indirect-broken-form", "The signup form on our site is submitting but nothing happens — can you figure out why?"),
    ("indirect-pretty-login", "Our login page looks terrible, can you make it not embarrassing?"),
]

# Tasks where the agent should NOT call search_skills.
# These are trivial, general knowledge, or conversational.
SHOULD_NOT_SEARCH: list[tuple[str, str]] = [
    # Math
    ("trivial-math-1", "What is 2 + 2?"),
    ("trivial-math-2", "Calculate 15% tip on a $47.50 bill."),
    ("trivial-math-3", "What's the square root of 144?"),

    # General programming knowledge
    ("trivial-code-1", "Explain the difference between a list and a tuple in Python."),
    ("trivial-code-2", "How do I rebase my branch onto main?"),
    ("trivial-code-3", "What does the `async` keyword do in JavaScript?"),
    ("trivial-code-4", "Write a function that checks if a string is a palindrome."),
    ("trivial-code-5", "What's the difference between == and === in JavaScript?"),

    # General knowledge / factual
    ("trivial-fact-1", "What year was Python first released?"),
    ("trivial-fact-2", "Who created the Git version control system?"),
    ("trivial-fact-3", "What does REST stand for?"),

    # Conversational
    ("trivial-chat-1", "Hello!"),
    ("trivial-chat-2", "Thanks for your help earlier."),
    ("trivial-chat-3", "Can you summarize what we discussed?"),

    # Simple coding tasks the model knows how to do
    ("trivial-simple-1", "Sort this list: [3, 1, 4, 1, 5, 9, 2, 6]"),
    ("trivial-simple-2", "What's the capital of France?"),
    ("trivial-simple-3", "Write a regex that matches email addresses."),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _get_backend() -> LLMBackend:
    backend_type = os.environ.get("E2E_BACKEND", "ollama")
    if backend_type == "claude":
        model = os.environ.get("E2E_MODEL", "claude-sonnet-4-6")
        return ClaudeBackend(model=model)
    model = os.environ.get("E2E_MODEL", "qwen2.5:7b")
    url = os.environ.get("E2E_OLLAMA_URL", "http://localhost:11434")
    return OllamaBackend(base_url=url, model=model)


@pytest.fixture(scope="session")
def catalog() -> SkillCatalog:
    if not SKILLS_DIR.is_dir():
        pytest.skip(f"Skills directory not found: {SKILLS_DIR}")
    cat = SkillCatalog()
    cat.load_directory(SKILLS_DIR)
    assert len(cat) > 0
    return cat


@pytest.fixture(scope="session")
def backend() -> LLMBackend:
    return _get_backend()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _did_search(result: AgentResult) -> bool:
    return any(tc.tool == "search_skills" for tc in result.tool_calls)


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:.0f}%" if total else "N/A"


async def _run_case(
    case_id: str,
    task: str,
    catalog: SkillCatalog,
    backend: LLMBackend,
    sem: asyncio.Semaphore,
) -> dict:
    """Run a single trigger test case, respecting the concurrency semaphore."""
    async with sem:
        result = await run_agent(task=task, catalog=catalog, backend=backend)
    searched = _did_search(result)
    tools = [tc.tool for tc in result.tool_calls]
    queries = [
        tc.arguments.get("query", "")
        for tc in result.tool_calls
        if tc.tool == "search_skills"
    ]
    return {
        "case_id": case_id,
        "task": task,
        "searched": searched,
        "tools": tools,
        "queries": queries,
        "response_preview": result.response[:200],
        "error": result.error,
    }


def _print_and_save_report(results: list[dict], label: str) -> None:
    """Print summary and save report to reports/."""
    correct = sum(1 for r in results if r["correct"])
    total = len(results)

    print(f"\n{'='*60}")
    print(f"TRIGGER {label}: {correct}/{total} ({_pct(correct, total)})")

    failures = [r for r in results if not r["correct"]]
    if failures:
        print(f"\nFailed:")
        for f in failures:
            print(f"  {f['case_id']}: {f['task'][:70]}")
            print(f"    searched={f['searched']}, queries={f.get('queries', [])}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Tests — run all cases concurrently within each group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_should_search(catalog: SkillCatalog, backend: LLMBackend):
    """All should-search cases run concurrently. Each must trigger search_skills."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
        _run_case(case_id, task, catalog, backend, sem)
        for case_id, task in SHOULD_SEARCH
    ]
    results = await asyncio.gather(*tasks)

    # Annotate correctness
    for r in results:
        r["expected"] = "search"
        r["correct"] = r["searched"]

    _print_and_save_report(results, "SHOULD SEARCH")

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"trigger_should_search_{timestamp}.json"
    report_path.write_text(json.dumps(results, indent=2))
    print(f"Report: {report_path}")

    # Collect failures for assertion message
    failures = [r for r in results if not r["correct"]]
    assert not failures, (
        f"{len(failures)}/{len(results)} cases failed to trigger search:\n"
        + "\n".join(
            f"  - {f['case_id']}: {f['task'][:60]}... (response: {f['response_preview'][:80]}...)"
            for f in failures
        )
    )


@pytest.mark.anyio
async def test_should_not_search(catalog: SkillCatalog, backend: LLMBackend):
    """All should-not-search cases run concurrently. None should trigger search_skills."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [
        _run_case(case_id, task, catalog, backend, sem)
        for case_id, task in SHOULD_NOT_SEARCH
    ]
    results = await asyncio.gather(*tasks)

    # Annotate correctness
    for r in results:
        r["expected"] = "no_search"
        r["correct"] = not r["searched"]

    _print_and_save_report(results, "SHOULD NOT SEARCH")

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"trigger_should_not_search_{timestamp}.json"
    report_path.write_text(json.dumps(results, indent=2))
    print(f"Report: {report_path}")

    # Collect failures for assertion message
    failures = [r for r in results if not r["correct"]]
    assert not failures, (
        f"{len(failures)}/{len(results)} cases incorrectly triggered search:\n"
        + "\n".join(
            f"  - {f['case_id']}: {f['task'][:60]}... (query: {f.get('queries', ['N/A'])})"
            for f in failures
        )
    )
