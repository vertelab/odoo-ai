# -*- coding: utf-8 -*-
"""
Interrupt Handler — AutoInterruptHandler (HITL-004).

For unattended execution (cron, server-action, mail).
Always approves, immediate timeout.
"""


class AutoInterruptHandler:
    """Auto-approving interrupt handler for unattended execution.

    Used for: cron jobs, server actions, email-triggered quests.
    Never blocks — always returns immediately.
    """

    async def ask(self, question: str, approval_type: str = "", context: str = "", timeout: float = 300) -> dict:
        """Auto-continue — no human available."""
        return {"action": "timeout", "reason": "unattended execution"}

    async def approve_tool(self, tool_name: str, risk_level: str, arguments: dict) -> bool:
        """Auto-approve all tool calls."""
        return True

    async def drain_steer(self) -> list[str]:
        """No mid-turn messages in unattended mode."""
        return []
