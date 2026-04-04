"""Level 3: End-to-end tests — agent loop with tool calling.

Requires either:
  - Ollama running locally (default: http://localhost:11434, model qwen2.5:7b)
  - Claude API key in ANTHROPIC_API_KEY env var

Configure via env vars:
  E2E_BACKEND=ollama|claude        (default: ollama)
  E2E_MODEL=<model-name>           (default: qwen2.5:7b for ollama, claude-sonnet-4-20250514 for claude)
  E2E_OLLAMA_URL=<base-url>        (default: http://localhost:11434)
  E2E_JUDGE_BACKEND=ollama|claude  (default: same as E2E_BACKEND)
  E2E_JUDGE_MODEL=<model-name>     (default: same as E2E_MODEL)
  E2E_CASES=<case-ids>             (comma-separated, default: all)

Run:
  uv run pytest tests/e2e/test_e2e.py -v
  uv run pytest tests/e2e/test_e2e.py -v -k "pdf-extract"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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
    """Resolve backend type, falling back to main E2E_BACKEND."""
    explicit = os.environ.get(f"{env_prefix}_BACKEND")
    if explicit:
        return explicit
    return os.environ.get("E2E_BACKEND", "ollama")


def _make_backend(env_prefix: str = "E2E") -> LLMBackend:
    backend_type = _get_backend_type(env_prefix)
    if backend_type == "claude":
        model = os.environ.get(f"{env_prefix}_MODEL", "claude-sonnet-4-20250514")
        return ClaudeBackend(model=model)
    else:
        model = os.environ.get(f"{env_prefix}_MODEL", "qwen2.5:7b")
        url = os.environ.get(
            f"{env_prefix}_OLLAMA_URL",
            os.environ.get("E2E_OLLAMA_URL", "http://localhost:11434"),
        )
        return OllamaBackend(base_url=url, model=model)


def _make_judge_backend() -> LLMBackend | None:
    """Create the judge backend. Inherits from main E2E_ settings if E2E_JUDGE_ not set."""
    if os.environ.get("E2E_SKIP_JUDGE"):
        return None
    return _make_backend(env_prefix="E2E_JUDGE")


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


@pytest.fixture(scope="session")
def judge_backend() -> LLMBackend | None:
    return _make_judge_backend()


# Parametrize tests from the JSON file
_cases = _load_test_cases()
_case_ids = [c["id"] for c in _cases]


@pytest.mark.anyio
@pytest.mark.parametrize("case", _cases, ids=_case_ids)
async def test_e2e(
    case: dict,
    catalog: SkillCatalog,
    backend: LLMBackend,
    judge_backend: LLMBackend | None,
):
    """Run one e2e test case: task → agent loop → grading."""
    # Run the agent
    result: AgentResult = await run_agent(
        task=case["task"],
        catalog=catalog,
        backend=backend,
    )

    # Grade the result
    grading: GradingResult = await grade(
        case=case,
        agent_result=result,
        judge_backend=judge_backend,
    )

    # Report
    summary = grading.summary()
    print(f"\n{'='*60}")
    print(f"Case: {case['id']}")
    print(f"Task: {case['task'][:80]}...")
    print(f"Expected skill: {case.get('expected_skill', 'none')}")
    print(f"Tool calls: {[(tc.tool, tc.arguments) for tc in result.tool_calls]}")
    print(f"Response preview: {result.response[:200]}...")
    print(f"Result: {'PASS' if grading.passed else 'FAIL'} ({summary['passed_assertions']}/{summary['total_assertions']})")
    if summary["failed_assertions"]:
        print(f"Failed:")
        for f in summary["failed_assertions"]:
            print(f"  - {f['text']}: {f['evidence']}")
    print(f"{'='*60}")

    assert grading.passed, (
        f"Case '{case['id']}' failed: "
        f"{summary['passed_assertions']}/{summary['total_assertions']} assertions passed. "
        f"Failures: {json.dumps(summary['failed_assertions'], indent=2)}"
    )
