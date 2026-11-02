# -*- coding: utf-8 -*-
"""Budget — the real cost guard lives on ``ai.coworker``, not here.

There is exactly **one** truth about cost in this module: the deterministic
budget fields on ``ai.coworker`` (``ai_agent_core/models/ai_coworker.py``).
The unit is **system tokens**, not USD:

- ``budget_exhausted`` — true when ``session_line_count`` reaches
  ``monthly_cap_mtokens`` × 1M. Derived from data, so a new month or a raised
  cap opens it automatically.
- ``budget_warning`` — true at 80 % of the same cap.
- ``daily_budget_exhausted`` / ``daily_budget_warning`` — the same two
  thresholds for ``daily_cap_mtokens``.
- ``check_cap()`` — the enforcement entry point. It reads those compute
  fields, sends at most one notification per period, and returns
  ``(warning, exhausted)`` so the caller can stop before the LLM call. It is
  called from every execution path (heartbeat, ``openai_api``, web UI,
  DM/channel, server action, powerbox, cron/watch).

``BudgetExhaustedError`` is kept for callers that want to signal a hard stop
with a typed exception.

Why the USD counter was removed
-------------------------------
``BudgetTracker`` / ``BudgetEnforcingProvider`` counted cost in USD from
hard-coded per-1k prices (``0.003`` / ``0.015``) that matched no provider.
Wiring them in would have created a **second truth** about cost, side by side
with ``budget_exhausted`` — and two truths about the same thing drift apart.
The prices belong to the gateway (``bifrost``), not to the core, and the core
must not name the bridge. The tracker also lived in memory and reset on
restart, while ``budget_exhausted`` is derived from stored data and therefore
stronger. The enforcement wrapper was ``async`` and would have had to wrap
every provider call — including the paths that already enforce the cap — for
no capability the cap does not already have.

See the change ``wire-orphaned-core-mechanisms`` (D2) for the full decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class BudgetState:
    """Snapshot of a budget — units follow whatever the caller tracks.

    Kept for reporting callers; the enforced budget is
    ``ai.coworker.budget_exhausted`` (system tokens).
    """
    agent_name: str = ""
    limit: float = 0.0
    used: float = 0.0
    remaining: float = 0.0
    month: str = ""              # YYYY-MM
    last_updated: float = 0.0    # Unix timestamp
    is_exhausted: bool = False
    total_calls: int = 0
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
