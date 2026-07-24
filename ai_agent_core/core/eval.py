# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Agent Evaluation — per-agent statistics and feedback loop (PAPER-006).

Agent performance MUST be measurable over time:
- Per-agent statistics: precision, recall, cost per correct answer
- False positive/negative tracking
- Evaluation runs with saved results
- Feedback loop for continuous improvement
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """A single evaluation test case."""
    input: str                       # User prompt
    expected_output: str             # Expected answer
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    category: str = "general"        # categorization
    difficulty: str = "medium"       # easy | medium | hard


@dataclass
class EvalResult:
    """Result of a single evaluation case."""
    case: EvalCase
    actual_output: str = ""
    passed: bool = False
    score: float = 0.0               # 0.0 – 1.0
    false_positives: list[str] = field(default_factory=list)
    false_negatives: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0


@dataclass
class EvalRun:
    """A complete evaluation run."""
    agent_name: str = ""
    model: str = ""
    timestamp: str = ""
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    accuracy: float = 0.0
    avg_score: float = 0.0
    avg_latency_ms: float = 0.0
    total_cost: float = 0.0
    results: list[EvalResult] = field(default_factory=list)
    false_positive_rate: float = 0.0
    false_negative_rate: float = 0.0
    cost_per_correct: float = 0.0

    def to_json(self) -> str:
        return json.dumps({
            "agent_name": self.agent_name,
            "model": self.model,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "accuracy": round(self.accuracy, 3),
            "avg_score": round(self.avg_score, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "total_cost": round(self.total_cost, 4),
            "fp_rate": round(self.false_positive_rate, 3),
            "fn_rate": round(self.false_negative_rate, 3),
            "cost_per_correct": round(self.cost_per_correct, 4),
        }, indent=2)


class AgentEvaluator:
    """Evaluate agent performance against a test suite.

    Usage:
        evaluator = AgentEvaluator()
        cases = [
            EvalCase(
                input="What is 2+2?",
                expected_contains=["4"],
                category="math",
            ),
        ]

        runner = evaluator.create_runner(agent_loop)
        run = await runner.run_eval(cases)
        print(f"Accuracy: {run.accuracy:.1%}")
    """

    def __init__(self):
        self._history: list[EvalRun] = []

    def create_runner(self, agent_loop, model: str = "default",
                      agent_name: str = "default") -> "EvalRunner":
        """Create an evaluation runner bound to an agent loop."""
        return EvalRunner(
            agent_loop=agent_loop,
            model=model,
            agent_name=agent_name,
        )

    @property
    def history(self) -> list[EvalRun]:
        return list(self._history)

    def add_run(self, run: EvalRun) -> None:
        self._history.append(run)


class EvalRunner:
    """Runs evaluation cases against an agent loop."""

    def __init__(self, agent_loop, model: str = "default",
                 agent_name: str = "default"):
        self.agent_loop = agent_loop
        self.model = model
        self.agent_name = agent_name

    async def run_eval(self, cases: list[EvalCase]) -> EvalRun:
        """Run all evaluation cases and return aggregated results."""
        run = EvalRun(
            agent_name=self.agent_name,
            model=self.model,
            timestamp=datetime.now().isoformat(),
            total_cases=len(cases),
        )

        for case in cases:
            result = await self._evaluate_case(case)
            run.results.append(result)
            if result.passed:
                run.passed += 1
            else:
                run.failed += 1

        # Calculate aggregate stats
        run.accuracy = run.passed / run.total_cases if run.total_cases > 0 else 0.0
        run.avg_score = (
            sum(r.score for r in run.results) / len(run.results)
            if run.results else 0.0
        )
        run.avg_latency_ms = (
            sum(r.latency_ms for r in run.results) / len(run.results)
            if run.results else 0.0
        )
        run.total_cost = sum(r.cost for r in run.results)

        total_fp = sum(len(r.false_positives) for r in run.results)
        total_fn = sum(len(r.false_negatives) for r in run.results)
        run.false_positive_rate = (
            total_fp / run.total_cases if run.total_cases > 0 else 0.0
        )
        run.false_negative_rate = (
            total_fn / run.total_cases if run.total_cases > 0 else 0.0
        )
        run.cost_per_correct = (
            run.total_cost / run.passed if run.passed > 0 else 0.0
        )

        _logger.info(
            "Eval complete: %s/%s — accuracy=%.1f%%, cost=$%.4f, "
            "latency=%.0fms avg",
            run.passed, run.total_cases,
            run.accuracy * 100, run.total_cost, run.avg_latency_ms,
        )

        return run

    async def _evaluate_case(self, case: EvalCase) -> EvalResult:
        """Evaluate a single test case."""
        start = time.time()

        try:
            response = await self.agent_loop.run(case.input)
            output = response.text or ""
        except Exception as e:
            return EvalResult(
                case=case,
                actual_output=f"ERROR: {e}",
                passed=False,
                errors=[str(e)],
            )

        latency_ms = (time.time() - start) * 1000
        output_lower = output.lower()

        # Score based on expected_contains / expected_not_contains
        score = 1.0
        fp = []
        fn = []

        # Check expected_contains
        for item in case.expected_contains:
            if item.lower() not in output_lower:
                fn.append(item)
                score -= 0.2

        # Check expected_not_contains
        for item in case.expected_not_contains:
            if item.lower() in output_lower:
                fp.append(item)
                score -= 0.3

        # Check expected_output exact match
        if case.expected_output:
            if case.expected_output.lower() in output_lower:
                # Bonus for containing expected output
                score = min(score + 0.1, 1.0)
            else:
                fn.append(f"expected: {case.expected_output[:100]}")

        score = max(0.0, min(score, 1.0))

        return EvalResult(
            case=case,
            actual_output=output,
            passed=score >= 0.8,
            score=score,
            false_positives=fp,
            false_negatives=fn,
            latency_ms=latency_ms,
            tokens_input=response.input_tokens,
            tokens_output=response.output_tokens,
            cost=(
                response.input_tokens / 1000 * 0.003
                + response.output_tokens / 1000 * 0.015
            ),
        )


# ---------------------------------------------------------------------------
# Trend Analysis
# ---------------------------------------------------------------------------

@dataclass
class EvalTrend:
    """Trend analysis across multiple eval runs."""
    runs: int = 0
    accuracy_trend: list[float] = field(default_factory=list)
    cost_trend: list[float] = field(default_factory=list)
    latency_trend: list[float] = field(default_factory=list)
    improving: bool = False       # Is accuracy improving?
    cost_increasing: bool = False  # Is cost going up?


def analyze_trend(runs: list[EvalRun]) -> EvalTrend:
    """Analyze performance trends across evaluation runs."""
    trend = EvalTrend(runs=len(runs))

    if len(runs) < 2:
        return trend

    trend.accuracy_trend = [r.accuracy for r in runs]
    trend.cost_trend = [r.total_cost for r in runs]
    trend.latency_trend = [r.avg_latency_ms for r in runs]

    # Check if accuracy is improving (simple linear regression slope)
    if len(trend.accuracy_trend) >= 2:
        slope = trend.accuracy_trend[-1] - trend.accuracy_trend[0]
        trend.improving = slope > 0.01  # At least 1% improvement

    # Check if costs are increasing
    if len(trend.cost_trend) >= 2:
        trend.cost_increasing = trend.cost_trend[-1] > trend.cost_trend[0]

    return trend
