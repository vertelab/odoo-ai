# -*- coding: utf-8 -*-
"""Unit tests for the OpenWorker permission gate (tasks 1.3-1.5).

Proves:
- Permission mode classification (DISCUSS / PLAN / INTERACTIVE / AUTO)
- RiskClass classification from tool names / risk levels
- The HITL gate: write tools require approval before execution
- Plan-before-action flow (todo_write + propose_plan)
- Standing rules (session allow/deny)
"""

import asyncio
import json
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, '/usr/share/odoo-ai')

from ai_agent_core.core.permission import (
    PermissionEngine,
    PermissionMode,
    RiskClass,
    classify,
    is_consequential,
    Decision,
)


class TestRiskClassification(unittest.TestCase):
    """RiskClass classification (task 1.3)."""

    def test_read_tools_are_read(self):
        self.assertEqual(classify("web_search"), RiskClass.READ)
        self.assertEqual(classify("calculator"), RiskClass.READ)
        self.assertEqual(classify("search_read", "read_only"), RiskClass.READ)

    def test_write_tools_are_write_local(self):
        self.assertEqual(classify("write_res_partner"), RiskClass.WRITE_LOCAL)
        self.assertEqual(classify("create_sale_order"), RiskClass.WRITE_LOCAL)

    def test_external_tools_flagged(self):
        self.assertEqual(classify("unlink_res_partner"), RiskClass.EXTERNAL)
        self.assertEqual(classify("delete_attachment"), RiskClass.EXTERNAL)

    def test_exec_tools_flagged(self):
        self.assertEqual(classify("run_shell"), RiskClass.EXEC)
        self.assertEqual(classify("eval_code"), RiskClass.EXEC)

    def test_risk_level_mapping(self):
        self.assertEqual(classify("some_tool", "write"), RiskClass.WRITE_LOCAL)
        self.assertEqual(classify("some_tool", "execute"), RiskClass.EXEC)
        self.assertEqual(classify("some_tool", "destructive"), RiskClass.EXTERNAL)

    def test_is_consequential(self):
        self.assertFalse(is_consequential(RiskClass.READ))
        self.assertTrue(is_consequential(RiskClass.WRITE_LOCAL))
        self.assertTrue(is_consequential(RiskClass.EXEC))
        self.assertTrue(is_consequential(RiskClass.EXTERNAL))


class TestPermissionEngineModes(unittest.TestCase):
    """Permission mode gating (task 1.3)."""

    def test_discuss_mode_read_only(self):
        engine = PermissionEngine(mode=PermissionMode.DISCUSS)
        d = engine.evaluate("search_read", {})
        self.assertTrue(d.allowed)
        d = engine.evaluate("write_res_partner", {"values": {}})
        self.assertFalse(d.allowed)
        self.assertFalse(d.needs_user)  # hard deny, no prompt

    def test_plan_mode_read_only(self):
        engine = PermissionEngine(mode=PermissionMode.PLAN)
        d = engine.evaluate("calculator", {})
        self.assertTrue(d.allowed)
        d = engine.evaluate("create_sale_order", {})
        self.assertFalse(d.allowed)

    def test_interactive_mode_asks_on_write(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        d = engine.evaluate("write_res_partner", {"values": {}})
        self.assertTrue(d.allowed)  # allowed but requires user
        self.assertTrue(d.needs_user)
        d = engine.evaluate("search_read", {})
        self.assertTrue(d.allowed)
        self.assertFalse(d.needs_user)

    def test_auto_mode_full_access(self):
        engine = PermissionEngine(mode=PermissionMode.AUTO)
        d = engine.evaluate("write_res_partner", {"values": {}})
        self.assertTrue(d.allowed)
        self.assertFalse(d.needs_user)

    def test_session_allowlist_skips_prompt(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        engine.allow_tool_for_session("write_res_partner")
        d = engine.evaluate("write_res_partner", {"values": {}})
        self.assertTrue(d.allowed)
        self.assertFalse(d.needs_user)
        self.assertTrue(d.rule)

    def test_command_allowlist_prefix(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        engine.allow_command_for_session("git status")
        d = engine.evaluate("run_shell", {"command": "git status --short"})
        self.assertTrue(d.allowed)
        self.assertFalse(d.needs_user)
        d = engine.evaluate("run_shell", {"command": "git push"})
        self.assertTrue(d.needs_user)


class TestPlanBeforeAction(unittest.TestCase):
    """todo_write + propose_plan tools exist and shape behavior (task 1.4)."""

    def test_todo_write_tool_exists(self):
        from ai_agent_core.core.tools import _tool_todo_write
        result = asyncio.run(_tool_todo_write(
            todos=[{"content": "Read data", "status": "in_progress"},
                   {"content": "Create report", "status": "pending"}]
        ))
        data = json.loads(result)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["todos"][0]["content"], "Read data")

    def test_todo_write_normalizes_status(self):
        from ai_agent_core.core.tools import _tool_todo_write
        result = asyncio.run(_tool_todo_write(items=[
            {"content": "Done item", "status": "completed"},
            {"content": "Weird status"},
        ]))
        data = json.loads(result)
        self.assertEqual(data["todos"][0]["status"], "done")
        self.assertEqual(data["todos"][1]["status"], "pending")

    def test_propose_plan_tool_exists(self):
        from ai_agent_core.core.tools import _tool_propose_plan
        result = _tool_propose_plan("Create the report")
        self.assertIn("plan", result.lower())

    def test_todolist_progress(self):
        from ai_agent_core.core.tools import TodoList
        tl = TodoList(items=[
            {"content": "a", "status": "done"},
            {"content": "b", "status": "pending"},
            {"content": "c", "status": "done"},
        ])
        self.assertEqual(tl.done_count, 2)
        self.assertEqual(tl.total_count, 3)
        self.assertAlmostEqual(tl.progress_pct, 66.666, places=1)


class TestOpenWorkerGate(unittest.TestCase):
    """The gate: nothing writes before approval (task 1.5, gap F1).

    Simulates the AgentLoop permission section: write tools with needs_user
    are only executed after approve_tool returns True.
    """

    def _simulate_gate(self, engine, tool_name, args, approve_answer):
        """Replicate the loop.py gate logic."""
        decision = engine.evaluate(tool_name, args, metadata=None)
        if not decision.allowed:
            return {"executed": False, "reason": "denied"}
        if decision.needs_user:
            if not approve_answer:
                return {"executed": False, "reason": "user_denied"}
            engine.allow_tool_for_session(tool_name)
        return {"executed": True}

    def test_write_requires_approval(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        # User rejects
        r = self._simulate_gate(engine, "write_res_partner", {"values": {}}, approve_answer=False)
        self.assertFalse(r["executed"])
        self.assertEqual(r["reason"], "user_denied")
        # User approves → executed, and now session-allowed
        r = self._simulate_gate(engine, "write_res_partner", {"values": {}}, approve_answer=True)
        self.assertTrue(r["executed"])

    def test_read_never_asks(self):
        engine = PermissionEngine(mode=PermissionMode.INTERACTIVE)
        r = self._simulate_gate(engine, "search_read", {}, approve_answer=False)
        self.assertTrue(r["executed"])

    def test_discuss_mode_hard_blocks_writes(self):
        engine = PermissionEngine(mode=PermissionMode.DISCUSS)
        r = self._simulate_gate(engine, "write_res_partner", {"values": {}}, approve_answer=True)
        self.assertFalse(r["executed"])
        self.assertEqual(r["reason"], "denied")

    def test_plan_mode_then_approval_flips_to_interactive(self):
        """PLAN mode: propose_plan approval switches to INTERACTIVE (loop.py)."""
        engine = PermissionEngine(mode=PermissionMode.PLAN)
        # In plan mode writes are denied
        r = self._simulate_gate(engine, "create_sale_order", {}, approve_answer=True)
        self.assertFalse(r["executed"])
        # Simulate propose_plan approval (loop.py sets INTERACTIVE)
        engine.set_mode(PermissionMode.INTERACTIVE)
        r = self._simulate_gate(engine, "create_sale_order", {}, approve_answer=True)
        self.assertTrue(r["executed"])


if __name__ == '__main__':
    unittest.main()
