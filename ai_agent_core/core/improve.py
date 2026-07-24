# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Structured Improvement Loop — guidance + references → iterate (TASK-003).

When quest output is incorrect, the system MUST support structured improvement:
- `guidance`: human-readable feedback ("missed Swedish customers")
- `references`: concrete examples [{filename, content}] for false positives/negatives
- Agent iterates with guidance + references → verify
- Max 3 iterations before escalating to human

Both API-backed and locally-anonymous flows.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class ImprovementGuidance:
    """Structured feedback for improvement."""
    text: str                           # Human-readable guidance
    false_positives: list[str] = field(default_factory=list)  # Examples that should NOT match
    false_negatives: list[str] = field(default_factory=list)  # Examples that SHOULD match
    references: list[dict] = field(default_factory=list)       # [{filename, content}]
    severity: str = "medium"            # low | medium | high | critical


@dataclass
class ImprovementResult:
    """Result of one improvement iteration."""
    iteration: int
    output_before: str
    output_after: str
    changes_made: list[str]        # What changed
    improvement_score: float = 0.0  # 0.0 = no change, 1.0 = perfect fix
    needs_escalation: bool = False
    escalation_reason: str = ""


@dataclass
class ImprovementRun:
    """Complete improvement run with all iterations."""
    guidance: ImprovementGuidance
    iterations: list[ImprovementResult] = field(default_factory=list)
    final_output: str = ""
    escalated: bool = False


class ImprovementLoop:
    """Structured feedback loop for agent output improvement.

    Usage:
        loop = ImprovementLoop(provider, max_iterations=3)
        result = await loop.improve(
            output="The report shows...",
            guidance=ImprovementGuidance(
                text="Missed Swedish customers",
                false_negatives=["Kalle's Kiosk", "Svenska AB"],
            ),
        )
    """

    def __init__(self, provider=None, max_iterations: int = 3):
        self.provider = provider
        self.max_iterations = max_iterations

    async def improve(
        self,
        output: str,
        guidance: ImprovementGuidance,
        original_prompt: str = "",
    ) -> ImprovementRun:
        """Run improvement iterations until output is correct or max reached.

        Args:
            output: Current (incorrect) output
            guidance: What's wrong and how to fix it
            original_prompt: The original user request

        Returns:
            ImprovementRun with all iterations and final output
        """
        run = ImprovementRun(guidance=guidance)
        current_output = output

        for i in range(1, self.max_iterations + 1):
            _logger.info(
                "Improvement iteration %d/%d — guidance: %s",
                i, self.max_iterations, guidance.text[:80],
            )

            # Build improvement prompt
            improved = await self._iterative_improve(
                current_output, guidance, original_prompt, iteration=i
            )

            # Calculate improvement score
            score = self._score_improvement(
                current_output, improved, guidance
            )

            result = ImprovementResult(
                iteration=i,
                output_before=current_output,
                output_after=improved,
                changes_made=self._diff_changes(current_output, improved),
                improvement_score=score,
            )
            run.iterations.append(result)

            # Check if good enough
            if score >= 0.9:
                _logger.info("Improvement converged at iteration %d (score=%.2f)", i, score)
                run.final_output = improved
                return run

            # Check if stuck (no improvement)
            if score < 0.1 and i > 1:
                run.needs_escalation = True
                run.escalated = True
                run.final_output = current_output
                _logger.warning(
                    "Improvement stuck at iteration %d — escalating", i
                )
                return run

            current_output = improved

        # Max iterations reached
        run.final_output = current_output
        run.escalated = True
        _logger.warning(
            "Improvement max iterations (%d) reached — escalating",
            self.max_iterations,
        )
        return run

    async def _iterative_improve(
        self,
        output: str,
        guidance: ImprovementGuidance,
        original_prompt: str,
        iteration: int,
    ) -> str:
        """One improvement iteration — apply guidance to output."""
        if self.provider:
            return await self._llm_improve(
                output, guidance, original_prompt, iteration
            )
        else:
            return self._rule_improve(output, guidance)

    async def _llm_improve(
        self,
        output: str,
        guidance: ImprovementGuidance,
        original_prompt: str,
        iteration: int,
    ) -> str:
        """Use LLM to improve output based on guidance."""
        from .provider import Message, Role

        fp_examples = "\n".join(
            f"- FALSE POSITIVE (should NOT match): {ex}"
            for ex in guidance.false_positives
        )
        fn_examples = "\n".join(
            f"- FALSE NEGATIVE (SHOULD match): {ex}"
            for ex in guidance.false_negatives
        )
        ref_examples = "\n".join(
            f"- Reference: {r.get('filename', '')}\n{r.get('content', '')[:500]}"
            for r in guidance.references
        )

        improvement_prompt = (
            f"Original request: {original_prompt}\n\n"
            f"Your current output:\n{output}\n\n"
            f"Improvement guidance:\n{guidance.text}\n\n"
            f"{'False Positives (remove these):\n' + fp_examples if guidance.false_positives else ''}"
            f"\n{'False Negatives (include these):\n' + fn_examples if guidance.false_negatives else ''}"
            f"\n{'Reference examples:\n' + ref_examples if guidance.references else ''}"
            f"\n\nIteration {iteration}: Improve the output based on the guidance above. "
            f"Return ONLY the improved output, no explanations."
        )

        messages = [Message(role=Role.USER, content=improvement_prompt)]

        try:
            response = await self.provider.chat(
                model="gpt-4o",
                messages=messages,
                system_prompt=(
                    "You are an improvement assistant. Given guidance, "
                    "improve the output. Be specific about fixes. "
                    "Return only the improved output."
                ),
                temperature=0.3,
                max_tokens=4096,
            )
            return response.text
        except Exception as e:
            _logger.warning("LLM improvement failed: %s — using rule-based", e)
            return self._rule_improve(output, guidance)

    def _rule_improve(self, output: str, guidance: ImprovementGuidance) -> str:
        """Rule-based improvement (no LLM). Applies simple fixes."""
        improved = output

        # Apply false negative fixes — add missing items
        for fn in guidance.false_negatives:
            if fn.lower() not in improved.lower():
                improved += f"\n- {fn}"

        # Apply false positive fixes — remove incorrect items
        for fp in guidance.false_positives:
            # Find and remove lines containing the false positive
            lines = improved.split("\n")
            improved = "\n".join(
                line for line in lines
                if fp.lower() not in line.lower()
            )

        return improved

    def _score_improvement(
        self,
        before: str,
        after: str,
        guidance: ImprovementGuidance,
    ) -> float:
        """Calculate improvement score (0.0–1.0)."""
        # If no guidance issues and output unchanged, it's perfect
        if (not guidance.false_positives and not guidance.false_negatives
                and before == after):
            return 1.0

        score = 0.0

        # 1. Did the output change? (must change to improve)
        if before != after:
            score += 0.3

        # 2. Are false positives removed?
        if guidance.false_positives:
            removed = sum(
                1 for fp in guidance.false_positives
                if fp.lower() not in after.lower()
            )
            score += (removed / len(guidance.false_positives)) * 0.35

        # 3. Are false negatives included?
        if guidance.false_negatives:
            included = sum(
                1 for fn in guidance.false_negatives
                if fn.lower() in after.lower()
            )
            score += (included / len(guidance.false_negatives)) * 0.35

        return min(score, 1.0)

    def _diff_changes(self, before: str, after: str) -> list[str]:
        """Return a list of human-readable change descriptions."""
        changes = []

        before_lines = set(before.split("\n"))
        after_lines = set(after.split("\n"))

        added = after_lines - before_lines
        removed = before_lines - after_lines

        for line in added:
            if line.strip():
                changes.append(f"Added: {line.strip()[:100]}")

        for line in removed:
            if line.strip():
                changes.append(f"Removed: {line.strip()[:100]}")

        if not changes and before != after:
            changes.append("Content modified (detail diff not shown)")

        return changes[:20]  # Limit
