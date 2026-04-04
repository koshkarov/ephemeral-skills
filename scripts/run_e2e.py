#!/usr/bin/env python3
"""CLI runner for e2e tests — run outside pytest for quick iteration.

Usage:
    # Run all cases with Ollama (default)
    uv run python scripts/run_e2e.py --skills-dir /path/to/skills

    # Run specific cases
    uv run python scripts/run_e2e.py --cases pdf-extract,mcp-server-build

    # Use Claude API
    uv run python scripts/run_e2e.py --backend claude --model claude-sonnet-4-20250514

    # Skip LLM-as-judge (only check tool trace)
    uv run python scripts/run_e2e.py --skip-judge

    # Save results to file
    uv run python scripts/run_e2e.py --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ephemeral_skills.agent import ClaudeBackend, LLMBackend, OllamaBackend, run_agent
from ephemeral_skills.catalog import SkillCatalog
from ephemeral_skills.grader import GradingResult, grade

TEST_CASES_PATH = Path(__file__).parent.parent / "tests" / "e2e" / "test_cases.json"


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


async def run_all(
    cases: list[dict],
    catalog: SkillCatalog,
    backend: LLMBackend,
    judge_backend: LLMBackend | None,
) -> list[dict]:
    results = []
    total = len(cases)

    for i, case in enumerate(cases, 1):
        case_id = case["id"]
        print(f"\n[{i}/{total}] Running: {case_id}")
        print(f"  Task: {case['task'][:100]}...")

        start = time.monotonic()
        agent_result = await run_agent(
            task=case["task"],
            catalog=catalog,
            backend=backend,
        )
        agent_time = time.monotonic() - start

        start = time.monotonic()
        grading_result = await grade(
            case=case,
            agent_result=agent_result,
            judge_backend=judge_backend,
        )
        grade_time = time.monotonic() - start

        summary = grading_result.summary()
        summary["agent_time_s"] = round(agent_time, 1)
        summary["grade_time_s"] = round(grade_time, 1)
        summary["response_preview"] = agent_result.response[:300]
        results.append(summary)

        # Print result
        status = "PASS" if grading_result.passed else "FAIL"
        emoji = "+" if grading_result.passed else "-"
        print(f"  [{emoji}] {status} ({summary['passed_assertions']}/{summary['total_assertions']} assertions, {agent_time:.1f}s)")

        if summary["failed_assertions"]:
            for f in summary["failed_assertions"]:
                print(f"      FAIL: {f['text']}")
                print(f"            {f['evidence']}")

        if agent_result.error:
            print(f"      ERROR: {agent_result.error}")

    return results


def print_summary(results: list[dict]):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} cases passed")
    if failed:
        print(f"\nFailed cases:")
        for r in results:
            if not r["passed"]:
                print(f"  - {r['case_id']}: {r['passed_assertions']}/{r['total_assertions']} assertions")
                for f in r["failed_assertions"]:
                    print(f"      {f['text']}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Run ephemeral skills e2e tests")
    parser.add_argument("--skills-dir", type=str, default="/home/brealx/repos/skills/skills")
    parser.add_argument("--cases", type=str, help="Comma-separated case IDs (default: all)")
    parser.add_argument("--backend", choices=["ollama", "claude"], default="ollama")
    parser.add_argument("--model", type=str, help="Model name")
    parser.add_argument("--ollama-url", type=str, default="http://localhost:11434")
    parser.add_argument("--judge-backend", choices=["ollama", "claude"], help="Judge backend (default: same as --backend)")
    parser.add_argument("--judge-model", type=str, help="Judge model (default: same as --model)")
    parser.add_argument("--skip-judge", action="store_true", help="Skip LLM-as-judge output grading")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    # Defaults
    if not args.model:
        args.model = "claude-sonnet-4-20250514" if args.backend == "claude" else "qwen2.5:7b"
    if not args.judge_backend:
        args.judge_backend = args.backend
    if not args.judge_model:
        args.judge_model = args.model

    # Load
    case_ids = [s.strip() for s in args.cases.split(",")] if args.cases else None
    cases = load_cases(case_ids)
    if not cases:
        print("No test cases to run.")
        sys.exit(1)

    catalog = SkillCatalog()
    count = catalog.load_directory(Path(args.skills_dir))
    print(f"Loaded {count} skills from {args.skills_dir}")

    backend = make_backend(args.backend, args.model, args.ollama_url)
    print(f"Agent backend: {args.backend} ({args.model})")

    judge_backend: LLMBackend | None = None
    if not args.skip_judge:
        judge_backend = make_backend(args.judge_backend, args.judge_model, args.ollama_url)
        print(f"Judge backend: {args.judge_backend} ({args.judge_model})")
    else:
        print("Judge: SKIPPED (tool trace assertions only)")

    print(f"Running {len(cases)} test case(s)...")

    # Run
    results = asyncio.run(run_all(cases, catalog, backend, judge_backend))

    # Summary
    print_summary(results)

    # Save
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"\nResults saved to {args.output}")

    # Exit code
    all_passed = all(r["passed"] for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
