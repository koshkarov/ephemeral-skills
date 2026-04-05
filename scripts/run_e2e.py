#!/usr/bin/env python3
"""CLI runner for e2e delivery tests.

Tests the full pipeline: agent searches for skills and reads the correct one.
No LLM-as-judge — assertions are purely deterministic (tool trace).
All cases run concurrently for speed.

Usage:
    uv run python scripts/run_e2e.py
    uv run python scripts/run_e2e.py --cases pdf-extract,slide-deck
    uv run python scripts/run_e2e.py --backend claude --model claude-sonnet-4-6
    uv run python scripts/run_e2e.py --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env from tests/e2e/
_env_file = Path(__file__).parent.parent / "tests" / "e2e" / ".env"
if _env_file.is_file():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

import os

from ephemeral_skills.agent import ClaudeBackend, LLMBackend, OllamaBackend, run_agent
from ephemeral_skills.catalog import SkillCatalog
from ephemeral_skills.grader import GradingResult, grade

TEST_CASES_PATH = Path(__file__).parent.parent / "tests" / "e2e" / "test_cases.json"

MAX_CONCURRENCY = int(os.environ.get("E2E_CONCURRENCY", "10"))


def load_cases(case_ids: list[str] | None = None) -> list[dict]:
    data = json.loads(TEST_CASES_PATH.read_text())
    cases = data["cases"]
    if case_ids:
        id_set = set(case_ids)
        cases = [c for c in cases if c["id"] in id_set]
        found = {c["id"] for c in cases}
        missing = id_set - found
        if missing:
            print(f"WARNING: cases not found: {missing}")
    return cases


def make_backend(backend_type: str, model: str, ollama_url: str) -> LLMBackend:
    if backend_type == "claude":
        return ClaudeBackend(model=model)
    return OllamaBackend(base_url=ollama_url, model=model)


async def _run_one(
    case: dict,
    catalog: SkillCatalog,
    backend: LLMBackend,
    sem: asyncio.Semaphore,
) -> dict:
    start = time.monotonic()
    async with sem:
        agent_result = await run_agent(task=case["task"], catalog=catalog, backend=backend)
    agent_time = time.monotonic() - start

    grading_result = await grade(case=case, agent_result=agent_result, judge_backend=None)
    summary = grading_result.summary()
    summary["agent_time_s"] = round(agent_time, 1)
    return summary


async def run_all(
    cases: list[dict],
    catalog: SkillCatalog,
    backend: LLMBackend,
) -> list[dict]:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [_run_one(case, catalog, backend, sem) for case in cases]
    results = await asyncio.gather(*tasks)

    # Print as they complete (all at once since gather waits for all)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        emoji = "+" if r["passed"] else "-"
        print(f"  [{emoji}] {status} {r['case_id']}: "
              f"{r['passed_assertions']}/{r['total_assertions']} assertions "
              f"({r['agent_time_s']}s)")
        if r["failed_assertions"]:
            for f in r["failed_assertions"]:
                print(f"      FAIL: {f['text']}")
                print(f"            {f['evidence']}")
        if r.get("error"):
            print(f"      ERROR: {r['error']}")

    return list(results)


def write_report(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2))
    print(f"\nReport saved to {path}")


def default_report_path() -> Path:
    reports_dir = Path(__file__).parent.parent / "reports"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return reports_dir / f"e2e_{timestamp}.json"


def print_summary(results: list[dict]):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print(f"\n{'='*60}")
    print(f"E2E DELIVERY: {passed}/{total} cases passed")
    if failed:
        print(f"\nFailed cases:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['case_id']}: {r['passed_assertions']}/{r['total_assertions']} assertions")
                for f in r["failed_assertions"]:
                    print(f"      {f['text']}")
    print(f"{'='*60}")


def main():
    global MAX_CONCURRENCY
    parser = argparse.ArgumentParser(description="Run ephemeral skills e2e delivery tests")
    parser.add_argument("--skills-dir", type=str, default=os.getenv("SKILLS_DIR", "/home/brealx/repos/skills/skills"))
    parser.add_argument("--cases", type=str, default=os.getenv("E2E_CASES"), help="Comma-separated case IDs")
    parser.add_argument("--backend", choices=["ollama", "claude"], default=os.getenv("E2E_BACKEND", "ollama"))
    parser.add_argument("--model", type=str, default=os.getenv("E2E_MODEL"))
    parser.add_argument("--ollama-url", type=str, default=os.getenv("E2E_OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY, help=f"Max concurrent LLM calls (default {MAX_CONCURRENCY})")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    if not args.model:
        args.model = "claude-sonnet-4-6" if args.backend == "claude" else "qwen2.5:7b"

    case_ids = [s.strip() for s in args.cases.split(",")] if args.cases else None
    cases = load_cases(case_ids)
    if not cases:
        print("No test cases to run.")
        sys.exit(1)

    catalog = SkillCatalog()
    count = catalog.load_directory(Path(args.skills_dir))
    print(f"Loaded {count} skills from {args.skills_dir}")

    backend = make_backend(args.backend, args.model, args.ollama_url)
    print(f"Backend: {args.backend} ({args.model})")
    print(f"Concurrency: {args.concurrency}")
    print(f"Running {len(cases)} test case(s)...\n")

    MAX_CONCURRENCY = args.concurrency

    results = asyncio.run(run_all(cases, catalog, backend))
    print_summary(results)

    report_path = Path(args.output) if args.output else default_report_path()
    write_report(results, report_path)

    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
