"""Level 3: End-to-end delivery tests — agent loop with tool calling.

Tests the full delivery pipeline: agent receives a task, searches for skills,
and reads the correct one. Does NOT grade the agent's output — that tests the
model, not the MCP server.

Requires either:
  - Ollama running locally (default: http://localhost:11434, model qwen2.5:7b)
  - Claude API key in ANTHROPIC_API_KEY env var

Run:
  uv run pytest tests/e2e/test_e2e.py -v
  uv run pytest tests/e2e/test_e2e.py -v -k "pdf-extract"
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

# Load .env from this directory
_env_file = Path(__file__).parent / ".env"
if _env_file.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import pytest

from ephemeral_skills.agent import (
    AgentResult,
    ClaudeBackend,
    LLMBackend,
    OllamaBackend,
    run_agent,
)
from ephemeral_skills.catalog import SkillCatalog
from ephemeral_skills.grader import GradingResult, grade

SKILLS_DIR = Path("/home/brealx/repos/skills/skills")
TEST_CASES_PATH = Path(__file__).parent / "test_cases.json"
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"

MAX_CONCURRENCY = int(os.environ.get("E2E_CONCURRENCY", "10"))


def _load_test_cases() -> list[dict]:
    data = json.loads(TEST_CASES_PATH.read_text())
    cases = data["cases"]
    # Filter by E2E_CASES env var if set
    selected = os.environ.get("E2E_CASES")
    if selected:
        ids = {s.strip() for s in selected.split(",")}
        cases = [c for c in cases if c["id"] in ids]
    return cases


def _get_backend_type(env_prefix: str = "E2E") -> str:
    explicit = os.environ.get(f"{env_prefix}_BACKEND")
    if explicit:
        return explicit
    return os.environ.get("E2E_BACKEND", "ollama")


def _make_backend(env_prefix: str = "E2E") -> LLMBackend:
    backend_type = _get_backend_type(env_prefix)
    if backend_type == "claude":
        model = os.environ.get(f"{env_prefix}_MODEL", "claude-sonnet-4-6")
        return ClaudeBackend(model=model)
    else:
        model = os.environ.get(f"{env_prefix}_MODEL", "qwen2.5:7b")
        url = os.environ.get(
            f"{env_prefix}_OLLAMA_URL",
            os.environ.get("E2E_OLLAMA_URL", "http://localhost:11434"),
        )
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
    return _make_backend()


_cases = _load_test_cases()


async def _run_case(
    case: dict,
    catalog: SkillCatalog,
    backend: LLMBackend,
    sem: asyncio.Semaphore,
) -> tuple[dict, AgentResult, GradingResult]:
    async with sem:
        result = await run_agent(task=case["task"], catalog=catalog, backend=backend)
    grading = await grade(case=case, agent_result=result, judge_backend=None)
    return case, result, grading


@pytest.mark.anyio
async def test_e2e_delivery(catalog: SkillCatalog, backend: LLMBackend):
    """Run all e2e cases concurrently. Assert delivery (tool trace) only."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [_run_case(case, catalog, backend, sem) for case in _cases]
    outcomes = await asyncio.gather(*tasks)

    results_collector = []
    failures = []

    for case, result, grading in outcomes:
        summary = grading.summary()
        results_collector.append(summary)

        status = "PASS" if grading.passed else "FAIL"
        print(f"\n[{status}] {case['id']}: "
              f"{summary['passed_assertions']}/{summary['total_assertions']} assertions")
        print(f"  Tool calls: {[(tc.tool, tc.arguments) for tc in result.tool_calls]}")

        if not grading.passed:
            for f in summary["failed_assertions"]:
                print(f"  FAIL: {f['text']}: {f['evidence']}")
            failures.append(summary)

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"e2e_{timestamp}.json"
    report_path.write_text(json.dumps(results_collector, indent=2))

    total = len(results_collector)
    passed = total - len(failures)
    print(f"\n{'='*60}")
    print(f"E2E DELIVERY: {passed}/{total} cases passed")
    if failures:
        print(f"\nFailed:")
        for f in failures:
            print(f"  - {f['case_id']}: {'; '.join(a['text'] for a in f['failed_assertions'])}")
    print(f"Report: {report_path}")
    print(f"{'='*60}")

    assert not failures, (
        f"{len(failures)}/{total} cases failed delivery:\n"
        + "\n".join(
            f"  - {f['case_id']}: {'; '.join(a['text'] for a in f['failed_assertions'])}"
            for f in failures
        )
    )
