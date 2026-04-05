"""Grading system for e2e test results.

Deterministic tool trace assertions only — did the agent call the right tools
and read the right skill? No LLM-as-judge needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .agent import AgentResult, LLMBackend, ToolCall


@dataclass
class AssertionResult:
    """Result of evaluating one assertion."""

    text: str
    passed: bool
    evidence: str
    assertion_type: str = "tool_trace"


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
            "all_assertions": [
                {"text": a.text, "passed": a.passed, "evidence": a.evidence, "type": a.assertion_type}
                for a in self.assertion_results
            ],
            "tool_calls": [
                {"tool": tc.tool, "arguments": tc.arguments}
                for tc in self.agent_result.tool_calls
            ],
            "turns": self.agent_result.turns,
            "response": self.agent_result.response,
            "error": self.agent_result.error,
        }


def _grade_tool_trace(
    tool_calls: list[ToolCall],
    trace_assertions: list[dict],
    expected_skill: str | None,
) -> list[AssertionResult]:
    """Grade tool trace assertions against the actual tool call sequence.

    expected_skill values:
      - "skill-name": agent must search and read this specific skill
      - "_any": agent should search but no specific skill is expected
      - None: agent should NOT call any tools (trivial task)
    """
    results: list[AssertionResult] = []

    if expected_skill is None:
        # Expect NO tool calls
        if len(tool_calls) == 0:
            results.append(AssertionResult(
                text="Agent should not call any tools for a trivial task",
                passed=True,
                evidence="No tool calls made",
            ))
        else:
            called = [f"{tc.tool}({json.dumps(tc.arguments)})" for tc in tool_calls]
            results.append(AssertionResult(
                text="Agent should not call any tools for a trivial task",
                passed=False,
                evidence=f"Agent made {len(tool_calls)} tool call(s): {', '.join(called)}",
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
        ))

    # Also check that the expected skill was ultimately read (skip for "_any")
    read_calls = [tc for tc in tool_calls if tc.tool == "read_skill"]
    read_names = [tc.arguments.get("name") for tc in read_calls]
    if expected_skill and expected_skill != "_any" and expected_skill not in read_names:
        results.append(AssertionResult(
            text=f"Agent reads the '{expected_skill}' skill",
            passed=False,
            evidence=f"Skills read: {read_names or 'none'}",
        ))

    return results


async def grade(
    case: dict,
    agent_result: AgentResult,
    judge_backend: LLMBackend | None = None,
) -> GradingResult:
    """Grade an agent result against a test case (tool trace only).

    The judge_backend parameter is kept for API compatibility but ignored.
    """
    assertions = case.get("assertions", {})
    expected_skill = case.get("expected_skill")

    all_results: list[AssertionResult] = []

    # Tool trace assertions (deterministic)
    trace_assertions = assertions.get("tool_trace", [])
    trace_results = _grade_tool_trace(
        agent_result.tool_calls, trace_assertions, expected_skill
    )
    all_results.extend(trace_results)

    # Check for agent errors
    if agent_result.error:
        all_results.append(AssertionResult(
            text="Agent completes without error",
            passed=False,
            evidence=f"Error: {agent_result.error}",
        ))

    passed = all(r.passed for r in all_results)

    return GradingResult(
        case_id=case["id"],
        passed=passed,
        assertion_results=all_results,
        agent_result=agent_result,
    )
