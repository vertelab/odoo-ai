# -*- coding: utf-8 -*-
"""Unit tests for ai_agent_core — provider, tools, loop, context."""

import asyncio
import json
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, patch, MagicMock

# Add ai_agent_core to path for standalone testing
import sys
sys.path.insert(0, '/usr/share/odoo-ai')


# ---------------------------------------------------------------------------
# Tool Tests (TOOL-001, TOOL-005)
# ---------------------------------------------------------------------------

class TestTool(unittest.TestCase):
    """Test Tool dataclass and serialization."""

    def setUp(self):
        from ai_agent_core.core.tools import Tool
        self.Tool = Tool

    def test_create_tool(self):
        tool = self.Tool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: "ok",
            risk_level="safe",
        )
        self.assertEqual(tool.name, "test_tool")
        self.assertEqual(tool.risk_level, "safe")
        self.assertEqual(tool.source, "custom")

    def test_to_openai(self):
        tool = self.Tool(
            name="calculator",
            description="Calculate math",
            parameters={"type": "object", "properties": {"expr": {"type": "string"}}},
            handler=lambda **kw: "42",
        )
        result = tool.to_openai()
        self.assertEqual(result["type"], "function")
        self.assertEqual(result["function"]["name"], "calculator")
        self.assertEqual(result["function"]["description"], "Calculate math")
        self.assertIn("expr", result["function"]["parameters"]["properties"])

    def test_to_anthropic(self):
        tool = self.Tool(
            name="calculator",
            description="Calculate math",
            parameters={"type": "object", "properties": {"expr": {"type": "string"}}},
            handler=lambda **kw: "42",
        )
        result = tool.to_anthropic()
        self.assertEqual(result["name"], "calculator")
        self.assertEqual(result["description"], "Calculate math")
        self.assertEqual(result["input_schema"]["properties"]["expr"]["type"], "string")

    def test_tool_execute_success(self):
        async def echo_handler(msg=""):
            return f"echo: {msg}"
        
        tool = self.Tool(
            name="echo",
            description="Echo",
            parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
            handler=echo_handler,
        )
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(tool.execute(msg="hello"))
        loop.close()
        self.assertEqual(result, "echo: hello")

    def test_tool_execute_error(self):
        async def fail_handler(**kw):
            raise Exception("boom")
        
        tool = self.Tool(
            name="failer",
            description="Always fails",
            parameters={"type": "object", "properties": {}},
            handler=fail_handler,
        )
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(tool.execute())
        loop.close()
        self.assertIn("Tool error", result)
        self.assertIn("boom", result)


class TestToolRegistry(unittest.TestCase):
    """Test ToolRegistry."""

    def setUp(self):
        from ai_agent_core.core.tools import Tool, ToolRegistry
        self.Tool = Tool
        self.ToolRegistry = ToolRegistry

    def test_register_and_get(self):
        reg = self.ToolRegistry()
        async def h(**kw): return "ok"
        tool = self.Tool(
            name="test",
            description="A test",
            parameters={},
            handler=h,
        )
        reg.register(tool)
        self.assertEqual(len(reg), 1)
        self.assertIn("test", reg)
        self.assertEqual(reg.get("test").name, "test")
        self.assertIsNone(reg.get("nonexistent"))

    def test_register_overwrites(self):
        reg = self.ToolRegistry()
        async def h1(): return "1"
        async def h2(): return "2"
        t1 = self.Tool(name="dup", description="first", parameters={}, handler=h1)
        t2 = self.Tool(name="dup", description="second", parameters={}, handler=h2)
        reg.register(t1)
        reg.register(t2)
        self.assertEqual(reg.get("dup").description, "second")

    def test_to_openai_empty(self):
        reg = self.ToolRegistry()
        self.assertEqual(reg.to_openai(), [])

    def test_to_openai_with_tools(self):
        reg = self.ToolRegistry()
        reg.register(self.Tool(
            name="t1", description="d1",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "ok",
        ))
        result = reg.to_openai()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["function"]["name"], "t1")

    def test_builtin_tools(self):
        from ai_agent_core.core.tools import builtin_tools
        tools = builtin_tools()
        self.assertGreaterEqual(len(tools), 2)
        names = {t.name for t in tools}
        self.assertIn("calculator", names)
        self.assertIn("echo", names)


# ---------------------------------------------------------------------------
# Provider Tests (PROV-001, PROV-002)
# ---------------------------------------------------------------------------

class TestMessage(unittest.TestCase):
    """Test Message serialization."""

    def setUp(self):
        from ai_agent_core.core.provider import Message, Role
        self.Message = Message
        self.Role = Role

    def test_to_openai_user(self):
        msg = self.Message(role=self.Role.USER, content="hello")
        result = msg.to_openai()
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["content"], "hello")

    def test_to_openai_assistant_with_tool_calls(self):
        msg = self.Message(
            role=self.Role.ASSISTANT,
            content="Let me calculate",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "calc", "arguments": '{"expr":"2+2"}'},
            }],
        )
        result = msg.to_openai()
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(len(result["tool_calls"]), 1)

    def test_to_openai_tool(self):
        msg = self.Message(
            role=self.Role.TOOL,
            content="4",
            tool_call_id="call_1",
            name="calc",
        )
        result = msg.to_openai()
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call_1")

    def test_to_anthropic_system_is_none(self):
        msg = self.Message(role=self.Role.SYSTEM, content="You are helpful")
        result = msg.to_anthropic()
        self.assertIsNone(result)

    def test_to_anthropic_tool_result(self):
        msg = self.Message(
            role=self.Role.TOOL,
            content="result text",
            tool_call_id="toolu_01",
        )
        result = msg.to_anthropic()
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["content"][0]["type"], "tool_result")
        self.assertEqual(result["content"][0]["tool_use_id"], "toolu_01")


# ---------------------------------------------------------------------------
# Context Tests (LOOP-004)
# ---------------------------------------------------------------------------

class TestContext(unittest.TestCase):
    """Test context management."""

    def setUp(self):
        from ai_agent_core.core.provider import Message, Role
        self.Message = Message
        self.Role = Role

    def test_estimate_tokens(self):
        from ai_agent_core.core.context import estimate_tokens
        msgs = [
            self.Message(role=self.Role.USER, content="hello world"),
            self.Message(role=self.Role.ASSISTANT, content="hi there"),
        ]
        tokens = estimate_tokens(msgs)
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, 10)

    def test_is_context_full(self):
        from ai_agent_core.core.context import is_context_full
        msgs = [self.Message(role=self.Role.USER, content="x" * 1000)]
        self.assertFalse(is_context_full(msgs, max_tokens=1000))

        big = [self.Message(role=self.Role.USER, content="x" * 600_000)]  # 600K chars / 4 = 150K tokens > 128K
        self.assertTrue(is_context_full(big, max_tokens=128_000))

    def test_summarize_preserves_recent(self):
        from ai_agent_core.core.context import summarize_history
        msgs = [
            self.Message(role=self.Role.USER, content=f"message {i}")
            for i in range(10)
        ]
        # Without a real provider, summarize_history requires one.
        # Test that it keeps recent messages when too few to summarize.
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            summarize_history(None, "test", msgs, keep_recent=4)
        )
        loop.close()
        # If provider is None, it'll fall through. Just verify no crash.
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# Interrupt Tests (HITL-004)
# ---------------------------------------------------------------------------

class TestAutoInterruptHandler(unittest.TestCase):
    """Test AutoInterruptHandler."""

    def setUp(self):
        from ai_agent_core.core.interrupt import AutoInterruptHandler
        self.handler = AutoInterruptHandler()

    def test_ask_returns_timeout(self):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            self.handler.ask("Should I proceed?")
        )
        loop.close()
        self.assertEqual(result["action"], "timeout")

    def test_approve_tool_always_true(self):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            self.handler.approve_tool("delete_all", "destructive", {})
        )
        loop.close()
        self.assertTrue(result)

    def test_drain_steer_empty(self):
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(self.handler.drain_steer())
        loop.close()
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Agent Loop Tests (LOOP-001)
# ---------------------------------------------------------------------------

class TestAgentLoop(unittest.TestCase):
    """Test AgentLoop with mock provider."""

    def setUp(self):
        from ai_agent_core.core.provider import AIProvider, ChatResponse, Message, Role
        from ai_agent_core.core.tools import Tool, ToolRegistry
        from ai_agent_core.core.loop import AgentLoop, AgentConfig

        # Mock provider that returns a fixed response
        class MockProvider(AIProvider):
            async def chat(self, model, messages, tools=None, system_prompt="", temperature=0.7, max_tokens=4096):
                return ChatResponse(
                    text="Mock response",
                    input_tokens=10,
                    output_tokens=5,
                    model=model,
                )

            async def chat_stream(self, model, messages, tools=None, system_prompt="", temperature=0.7, max_tokens=4096):
                from ai_agent_core.core.provider import TokenEvent
                for token in ["Mock", " ", "response"]:
                    yield TokenEvent(type="token", token=token)
                yield TokenEvent(type="done", finish_reason="stop")

        self.MockProvider = MockProvider
        self.Tool = Tool
        self.ToolRegistry = ToolRegistry
        self.AgentLoop = AgentLoop
        self.AgentConfig = AgentConfig

    def test_simple_run(self):
        provider = self.MockProvider()
        tools = self.ToolRegistry()
        loop_obj = self.AgentLoop(
            provider=provider, tools=tools,
            config=self.AgentConfig(max_rounds=5),
        )

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(loop_obj.run("hello"))
        loop.close()

        self.assertEqual(result.text, "Mock response")
        self.assertEqual(result.input_tokens, 10)
        self.assertEqual(result.output_tokens, 5)

    def test_run_with_tool_calls(self):
        call_count = [0]

        async def counter_tool(**kw):
            call_count[0] += 1
            return f"called {call_count[0]}"

        provider = self.MockProvider()

        # Override to return a tool call first, then text
        original_chat = provider.chat
        call_phase = [0]

        async def chat_with_tool(*args, **kwargs):
            from ai_agent_core.core.provider import ChatResponse, ToolCall
            call_phase[0] += 1
            if call_phase[0] == 1:
                return ChatResponse(
                    text="",
                    tool_calls=[ToolCall(id="t1", name="counter", arguments={})],
                    input_tokens=5,
                    output_tokens=3,
                )
            return ChatResponse(text="Done after tool", input_tokens=5, output_tokens=3)

        provider.chat = chat_with_tool

        tools = self.ToolRegistry()
        tools.register(self.Tool(
            name="counter",
            description="Count calls",
            parameters={"type": "object", "properties": {}},
            handler=counter_tool,
            risk_level="safe",
        ))

        loop_obj = self.AgentLoop(provider=provider, tools=tools, config=self.AgentConfig(max_rounds=5))
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(loop_obj.run("test"))
        loop.close()

        self.assertEqual(result.text, "Done after tool")
        self.assertEqual(call_count[0], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
