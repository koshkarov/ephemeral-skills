"""Grading system for e2e test results.

Two types of assertions:
1. Tool trace assertions (deterministic) — did the agent call the right tools?
2. Output assertions (LLM-as-judge) — does the response follow the skill's guidance?
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .agent import AgentResult, LLMBackend, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class AssertionResult:
    """Result of evaluating one assertion."""

    text: str
    passed: bool
    evidence: str
    assertion_type: str  # "tool_trace" or "output"


@dataclass
class GradingResult:
    """Full grading result for one test case."""

    case_id: str
    passed: bool
    assertion_results: list[AssertionResult]
    agent_result: AgentResult

    @property
    def pass_rate(self) -> float:
        if not self.assertion_results:
            return 1.0
        return sum(1 for a in self.assertion_results if a.passed) / len(self.assertion_results)

    def summary(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "total_assertions": len(self.assertion_results),
            "passed_assertions": sum(1 for a in self.assertion_results if a.passed),
            "failed_assertions": [
                {"text": a.text, "evidence": a.evidence}
                for a in self.assertion_results
                if not a.passed
            ],
            "tool_calls": [
                {"tool": tc.tool, "arguments": tc.arguments}
                for tc in self.agent_result.tool_calls
            ],
            "turns": self.agent_result.turns,
            "error": self.agent_result.error,
        }


# --- Tool trace grading (deterministic) ---


def _grade_tool_trace(
    tool_calls: list[ToolCall],
    trace_assertions: list[dict],
    expected_skill: str | None,
) -> list[AssertionResult]:
    """Grade tool trace assertions against the actual tool call sequence."""
    results: list[AssertionResult] = []

    if expected_skill is None:
        # Expect NO tool calls
        if len(tool_calls) == 0:
            results.append(AssertionResult(
                text="Agent should not call any tools for a trivial task",
                passed=True,
                evidence="No tool calls made",
                assertion_type="tool_trace",
            ))
        else:
            called = [f"{tc.tool}({json.dumps(tc.arguments)})" for tc in tool_calls]
            results.append(AssertionResult(
                text="Agent should not call any tools for a trivial task",
                passed=False,
                evidence=f"Agent made {len(tool_calls)} tool call(s): {', '.join(called)}",
                assertion_type="tool_trace",
            ))
        return results

    for assertion in trace_assertions:
        expected_tool = assertion["tool"]
        args_contain = assertion.get("args_contain", {})

        # Find a matching tool call
        matched = False
        match_evidence = ""
        for tc in tool_calls:
            if tc.tool != expected_tool:
                continue
            # Check args_contain
            args_match = True
            for key, expected_values in args_contain.items():
                actual_value = tc.arguments.get(key, "")
                if isinstance(expected_values, list):
                    # At least one of the expected values should appear in the actual value
                    actual_lower = str(actual_value).lower()
                    if not any(v.lower() in actual_lower for v in expected_values):
                        args_match = False
                        break
                else:
                    if str(expected_values).lower() != str(actual_value).lower():
                        args_match = False
                        break
            if args_match:
                matched = True
                match_evidence = f"Found call: {tc.tool}({json.dumps(tc.arguments)})"
                break

        desc = f"Agent calls {expected_tool}"
        if args_contain:
            desc += f" with args containing {json.dumps(args_contain)}"

        results.append(AssertionResult(
            text=desc,
            passed=matched,
            evidence=match_evidence if matched else f"Not found. Actual calls: {[tc.tool for tc in tool_calls]}",
            assertion_type="tool_trace",
        ))

    # Also check that the expected skill was ultimately read
    read_calls = [tc for tc in tool_calls if tc.tool == "read_skill"]
    read_names = [tc.arguments.get("name") for tc in read_calls]
    if expected_skill and expected_skill not in read_names:
        results.append(AssertionResult(
            text=f"Agent reads the '{expected_skill}' skill",
            passed=False,
            evidence=f"Skills read: {read_names or 'none'}",
            assertion_type="tool_trace",
        ))

    return results


# --- Output grading (LLM-as-judge) ---


JUDGE_PROMPT = """\
You are an evaluator. Given an AI agent's response to a task, evaluate each \
assertion below. For each assertion, respond with PASS or FAIL and brief evidence.

## Task
{task}

## Agent's Response
{response}

## Assertions to evaluate
{assertions}

## Output format
Respond with a JSON array. Each element must have:
- "assertion": the assertion text (copied exactly)
- "passed": true or false
- "evidence": 1-2 sentence explanation

Example:
[
  {{"assertion": "Response mentions pdfplumber", "passed": true, "evidence": "The response recommends using pdfplumber for text extraction."}},
  {{"assertion": "Response includes code", "passed": false, "evidence": "The response describes the approach in prose but does not include any code snippets."}}
]

Respond ONLY with the JSON array, no other text."""


async def _grade_output_with_llm(
    agent_result: AgentResult,
    output_assertions: list[str],
    judge_backend: LLMBackend,
) -> list[AssertionResult]:
    """Use an LLM to grade output assertions."""
    if not output_assertions:
        return []

    assertions_text = "\n".join(f"- {a}" for a in output_assertions)
    prompt = JUDGE_PROMPT.format(
        task=agent_result.task,
        response=agent_result.response,
        assertions=assertions_text,
    )

    try:
        response = await judge_backend.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        raw = response["content"].strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        grades = json.loads(raw)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse judge response: %s", e)
        return [
            AssertionResult(
                text=a,
                passed=False,
                evidence=f"Judge parse error: {e}",
                assertion_type="output",
            )
            for a in output_assertions
        ]
    except Exception as e:
        logger.warning("Judge LLM call failed: %s", e)
        return [
            AssertionResult(
                text=a,
                passed=False,
                evidence=f"Judge error: {e}",
                assertion_type="output",
            )
            for a in output_assertions
        ]

    results = []
    # Map grades back to assertions
    for assertion_text in output_assertions:
        matched_grade = None
        for g in grades:
            if g.get("assertion") == assertion_text:
                matched_grade = g
                break
        # Fallback: match by index if assertion text doesn't match exactly
        if matched_grade is None:
            idx = output_assertions.index(assertion_text)
            if idx < len(grades):
                matched_grade = grades[idx]

        if matched_grade:
            results.append(AssertionResult(
                text=assertion_text,
                passed=bool(matched_grade.get("passed", False)),
                evidence=matched_grade.get("evidence", "No evidence provided"),
                assertion_type="output",
            ))
        else:
            results.append(AssertionResult(
                text=assertion_text,
                passed=False,
                evidence="Judge did not evaluate this assertion",
                assertion_type="output",
            ))

    return results


# --- Top-level grading ---


async def grade(
    case: dict,
    agent_result: AgentResult,
    judge_backend: LLMBackend | None = None,
) -> GradingResult:
    """Grade an agent result against a test case.

    Args:
        case: Test case dict from test_cases.json
        agent_result: Result from run_agent()
        judge_backend: LLM backend for output assertions (optional — skips output
                       grading if not provided)
    """
    assertions = case.get("assertions", {})
    expected_skill = case.get("expected_skill")

    all_results: list[AssertionResult] = []

    # 1. Tool trace assertions (deterministic, no LLM needed)
    trace_assertions = assertions.get("tool_trace", [])
    trace_results = _grade_tool_trace(
        agent_result.tool_calls, trace_assertions, expected_skill
    )
    all_results.extend(trace_results)

    # 2. Output assertions (LLM-as-judge)
    output_assertions = assertions.get("output", [])
    if output_assertions and judge_backend:
        output_results = await _grade_output_with_llm(
            agent_result, output_assertions, judge_backend
        )
        all_results.extend(output_results)
    elif output_assertions and not judge_backend:
        for a in output_assertions:
            all_results.append(AssertionResult(
                text=a,
                passed=True,
                evidence="Skipped (no judge backend provided)",
                assertion_type="output",
            ))

    # Check for agent errors
    if agent_result.error:
        all_results.append(AssertionResult(
            text="Agent completes without error",
            passed=False,
            evidence=f"Error: {agent_result.error}",
            assertion_type="tool_trace",
        ))

    passed = all(r.passed for r in all_results)

    return GradingResult(
        case_id=case["id"],
        passed=passed,
        assertion_results=all_results,
        agent_result=agent_result,
    )
