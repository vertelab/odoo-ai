# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Budget Enforcement — hard stops at monthly limits (PAPER-004).

Every agent MUST have a configurable budget:
- `budget_limit` per agent per month (in USD)
- When budget exhausted: agent stops, no further LLM calls
- Budget usage MUST be visible in agent dashboard
- Budget is a HARD stop, not a notification
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class BudgetState:
    """Current budget state for an agent."""
    agent_name: str = ""
    limit: float = 0.0           # USD per month
    used: float = 0.0            # USD used this month
    remaining: float = 0.0       # USD remaining
    month: str = ""              # YYYY-MM
    last_updated: float = 0.0    # Unix timestamp
    is_exhausted: bool = False
    total_calls: int = 0         # Total LLM calls this month
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_per_1k_input: float = 0.003
    cost_per_1k_output: float = 0.015

    @property
    def percent_used(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(self.used / self.limit * 100, 100.0)

    def to_json(self) -> str:
        return json.dumps({
            "agent_name": self.agent_name,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "percent_used": self.percent_used,
            "is_exhausted": self.is_exhausted,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }, indent=2)


class BudgetExhaustedError(Exception):
    """Raised when attempting to make an LLM call with exhausted budget."""
    pass


class BudgetTracker:
    """Tracks and enforces agent budgets.

    Usage:
        tracker = BudgetTracker(limit=50.0)  # $50/month
        tracker.record_call(input_tokens=100, output_tokens=50)

        if tracker.is_exhausted:
            raise BudgetExhaustedError("Monthly budget of $50.00 exceeded")
    """

    def __init__(
        self,
        agent_name: str = "default",
        limit: float = 0.0,
        cost_per_1k_input: float = 0.003,
        cost_per_1k_output: float = 0.015,
        current_used: float = 0.0,
    ):
        self.agent_name = agent_name
        self.limit = limit
        self.used = current_used
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_updated = time.time()
        self._month = datetime.now().strftime("%Y-%m")

    @property
    def remaining(self) -> float:
        if self.limit <= 0:
            return float("inf")
        return max(0.0, self.limit - self.used)

    @property
    def is_exhausted(self) -> bool:
        if self.limit <= 0:
            return False  # No limit set
        return self.used >= self.limit

    @property
    def percent_used(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(self.used / self.limit * 100, 100.0)

    def record_call(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float:
        """Record an LLM call and return the cost.

        Raises BudgetExhaustedError if budget is exceeded.
        """
        cost = self._calculate_cost(input_tokens, output_tokens)

        if self.limit > 0 and self.used + cost > self.limit:
            _logger.warning(
                "Budget exhausted for agent '%s': used=%.4f, limit=%.2f, "
                "attempted=%.4f",
                self.agent_name, self.used, self.limit, cost,
            )
            raise BudgetExhaustedError(
                f"Monthly budget of ${self.limit:.2f} exhausted "
                f"(used ${self.used:.2f}, attempted ${cost:.4f})"
            )

        self.used += cost
        self.total_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.last_updated = time.time()

        _logger.debug(
            "Budget: agent='%s' call_cost=%.4f total=%.2f/%.2f (%.1f%%) "
            "tokens: in=%d out=%d",
            self.agent_name, cost, self.used, self.limit,
            self.percent_used, input_tokens, output_tokens,
        )

        return cost

    def estimate_call_cost(
        self,
        input_tokens: int,
        output_tokens: int = 0,
    ) -> float:
        """Estimate cost without recording."""
        return self._calculate_cost(input_tokens, output_tokens)

    def can_afford(self, input_tokens: int, output_tokens: int = 0) -> bool:
        """Check if there's enough budget for this call."""
        if self.limit <= 0:
            return True
        cost = self._calculate_cost(input_tokens, output_tokens)
        return self.used + cost <= self.limit

    def reset_month(self) -> None:
        """Reset budget for a new month."""
        self.used = 0.0
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._month = datetime.now().strftime("%Y-%m")
        self.last_updated = time.time()
        _logger.info(
            "Budget reset for agent '%s' — new month %s, limit=%.2f",
            self.agent_name, self._month, self.limit,
        )

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD."""
        input_cost = (input_tokens / 1000.0) * self.cost_per_1k_input
        output_cost = (output_tokens / 1000.0) * self.cost_per_1k_output
        return input_cost + output_cost

    def get_state(self) -> BudgetState:
        """Return current budget state."""
        return BudgetState(
            agent_name=self.agent_name,
            limit=self.limit,
            used=self.used,
            remaining=self.remaining,
            month=self._month,
            last_updated=self.last_updated,
            is_exhausted=self.is_exhausted,
            total_calls=self.total_calls,
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
            cost_per_1k_input=self.cost_per_1k_input,
            cost_per_1k_output=self.cost_per_1k_output,
        )


class BudgetEnforcingProvider:
    """Wraps an AIProvider with budget enforcement.

    Every chat() call is checked against the budget BEFORE the API call.
    If insufficient budget, BudgetExhaustedError is raised immediately
    (no wasted API call).

    Usage:
        provider = BifrostProvider()
        budget = BudgetTracker(limit=50.0)
        enforcing = BudgetEnforcingProvider(provider, budget)

        # This checks budget first, then calls provider
        response = await enforcing.chat(...)
    """

    def __init__(self, provider, budget: BudgetTracker):
        self._provider = provider
        self._budget = budget

    async def chat(self, model, messages, tools=None, system_prompt="",
                   temperature=0.7, max_tokens=4096):
        """Chat with budget enforcement."""
        # Estimate input tokens
        input_tokens = sum(len(m.content or "") for m in messages if hasattr(m, 'content'))
        input_tokens = input_tokens // 4 + 200  # rough estimate + system prompt

        # Check budget
        if not self._budget.can_afford(input_tokens, max_tokens):
            raise BudgetExhaustedError(
                f"Budget exhausted: ${self._budget.used:.2f} used of "
                f"${self._budget.limit:.2f} limit"
            )

        # Make the call
        response = await self._provider.chat(
            model=model,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Record the cost
        self._budget.record_call(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

        return response

    async def chat_stream(self, model, messages, tools=None, system_prompt="",
                          temperature=0.7, max_tokens=4096):
        """Streaming with budget enforcement."""
        input_tokens = sum(len(m.content or "") for m in messages if hasattr(m, 'content'))
        input_tokens = input_tokens // 4 + 200

        # Pre-authorize the estimated cost
        if not self._budget.can_afford(input_tokens, max_tokens):
            raise BudgetExhaustedError(
                f"Budget exhausted: ${self._budget.used:.2f} used of "
                f"${self._budget.limit:.2f} limit"
            )

        output_tokens = 0
        async for event in self._provider.chat_stream(
            model=model,
            messages=messages,
            tools=tools,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if hasattr(event, 'type') and event.type == "token":
                output_tokens += len(event.token) // 4  # rough estimate
            yield event

        # Record after stream completes
        self._budget.record_call(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def __getattr__(self, name):
        """Delegate other attributes to wrapped provider."""
        return getattr(self._provider, name)
