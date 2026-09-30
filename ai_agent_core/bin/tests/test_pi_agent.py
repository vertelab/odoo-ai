# -*- coding: utf-8 -*-
"""Enhetstester för pi-agent (ai_agent_core/bin/pi-agent)."""

import json
import os
import sys
import tempfile
import unittest

# Importera pi-agent som modul (den är ett CLI-script utan .py-ändelse)
_BIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../bin
sys.path.insert(0, _BIN_DIR)

import importlib.util
from importlib.machinery import SourceFileLoader
_pi_path = os.path.join(_BIN_DIR, "pi-agent")
mod = SourceFileLoader("pi_agent", _pi_path).load_module()


def _cfg(**kw):
    class Args:
        base_url = kw.get("base_url", "http://odoo.test")
        api_key = kw.get("api_key", "test-key")
        coworker = kw.get("coworker", "5")
        skills = kw.get("skills", "")
        task = kw.get("task", None)
        callback = kw.get("callback", None)
        abort_poll = kw.get("abort_poll", None)
        timeout = kw.get("timeout", 60)
        json = kw.get("json", False)
    return mod.Config(Args())


class MockHTTP:
    """Mockad HTTP-klient — simulerar Odoo openai_api-svar."""

    def __init__(self, responses=None, task_state="running"):
        self.responses = responses or []
        self.calls = 0
        self.posted = []
        self.task_state = task_state

    def post_json(self, path, payload):
        self.calls += 1
        self.posted.append((path, payload))
        if self.responses:
            return self.responses.pop(0)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "session_id": 1}

    def get_json(self, path):
        if path.startswith("/pi/task/"):
            return {"task": {"state": self.task_state}}
        return {}


class TestTools(unittest.TestCase):

    def test_bash(self):
        out = mod.tool_bash({"cmd": "echo pi-agent-funkar"})
        self.assertIn("pi-agent-funkar", out)

    def test_bash_missing_cmd(self):
        out = mod.tool_bash({})
        self.assertIn("krävs", out)

    def test_read_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "test.txt")
            w = mod.tool_write({"path": p, "content": "hello"})
            self.assertIn("5 tecken", w)
            r = mod.tool_read({"path": p})
            self.assertEqual(r, "hello")

    def test_unknown_tool(self):
        out = mod.execute_tool("finns_inte", {})
        self.assertIn("okänt verktyg", out)

    def test_list_tools_markdown(self):
        md = mod.tool_markdown()
        self.assertIn("# Verktyg", md)
        self.assertIn("## bash", md)
        self.assertIn("## salt", md)
        self.assertIn("## ssh", md)
        self.assertIn("## screenshot", md)


class TestSkills(unittest.TestCase):

    def test_build_system_prompt(self):
        skills = [{"technical_name": "saltstack", "instruction": "# Salt"}]
        prompt = mod.build_system_prompt("Bas", skills)
        self.assertIn("Bas", prompt)
        self.assertIn("--- saltstack ---", prompt)
        self.assertIn("# Salt", prompt)

    def test_empty_skills(self):
        prompt = mod.build_system_prompt("", [])
        self.assertIn("pi-agent", prompt)


class TestAgentLoop(unittest.TestCase):

    def test_plain_response(self):
        http = MockHTTP(responses=[{
            "choices": [{"message": {"role": "assistant", "content": "hej"}}],
            "session_id": 1,
        }])
        loop = mod.AgentLoop(_cfg(), http, system_prompt="S")
        result = loop.run("fråga")
        self.assertEqual(result, "hej")
        # systemprompt + user skickas
        self.assertEqual(http.posted[0][1]["messages"][0]["role"], "system")

    def test_tool_calls_loop(self):
        """LLM vill köra bash → agenten exekverar lokalt → role:tool."""
        http = MockHTTP(responses=[
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"cmd": "echo hej"})}}]}}],
             "session_id": 1},
            {"choices": [{"message": {"role": "assistant", "content": "klar"}}], "session_id": 1},
        ])
        loop = mod.AgentLoop(_cfg(), http, system_prompt="S")
        result = loop.run("test")
        self.assertEqual(result, "klar")
        # Andra anropet har role:tool med output
        second = http.posted[1][1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("hej", second[-1]["content"])
        # assistant-meddelandet med tool_calls bevaras
        self.assertEqual(second[-2]["role"], "assistant")
        self.assertTrue(second[-2].get("tool_calls"))

    def test_hitl_approved(self):
        """HITL via tool_calls: on_hitl godkänner → role:tool approved."""
        http = MockHTTP(responses=[
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "h1", "type": "function",
                "function": {"name": "request_hitl_approval",
                             "arguments": json.dumps({"question": "Får jag?"})}}]}}],
             "session_id": 1},
            {"choices": [{"message": {"role": "assistant", "content": "utförd"}}], "session_id": 1},
        ])
        loop = mod.AgentLoop(_cfg(), http, system_prompt="S",
                             on_hitl=lambda name, args: "approved")
        result = loop.run("ändra")
        self.assertEqual(result, "utförd")
        self.assertEqual(http.posted[1][1]["messages"][-1]["content"], "approved")

    def test_hitl_denied(self):
        http = MockHTTP(responses=[
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "h1", "type": "function",
                "function": {"name": "request_hitl_approval",
                             "arguments": json.dumps({"question": "Får jag?"})}}]}}],
             "session_id": 1},
            {"choices": [{"message": {"role": "assistant", "content": "avstod"}}], "session_id": 1},
        ])
        loop = mod.AgentLoop(_cfg(), http, system_prompt="S",
                             on_hitl=lambda name, args: "denied")
        result = loop.run("ändra")
        self.assertEqual(result, "avstod")
        self.assertEqual(http.posted[1][1]["messages"][-1]["content"], "denied")


class TestOrchestrated(unittest.TestCase):

    def test_fetch_task_and_callback(self):
        http = MockHTTP()
        task = mod.fetch_task(http, 42)
        self.assertIsNotNone(task)

    def test_check_abort(self):
        http = MockHTTP(task_state="aborting")
        self.assertTrue(mod.check_abort(http, 42))
        http2 = MockHTTP(task_state="running")
        self.assertFalse(mod.check_abort(http2, 42))


class TestServe(unittest.TestCase):
    """Server-läge: portallokering + ServeState + HITL-answer."""

    def _cfg(self):
        class Args:
            base_url = "http://odoo.test"
            api_key = "k"
            coworker = "5"
            skills = ""
            task = None
            callback = None
            abort_poll = None
            timeout = 60
            json = False
            mode = "serve"
            port = 9200
            name = "t"
        return mod.Config(Args())

    def test_find_free_port(self):
        ports = mod.find_free_port(9500, count=2)
        self.assertEqual(len(ports), 2)
        self.assertEqual(ports[0], 9500)
        # Andra porten är 9501
        self.assertEqual(ports[1], 9501)

    def test_find_free_port_skips_occupied(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 9550))
        s.listen(1)
        try:
            ports = mod.find_free_port(9550, count=1)
            self.assertEqual(ports, [9551])  # hoppar över upptagen 9550
        finally:
            s.close()

    def test_serve_state_events(self):
        state = mod.ServeState(self._cfg(), MockHTTP())
        state.emit("status", status="running")
        state.emit("response", content="hej")
        self.assertEqual(len(state.events), 2)
        self.assertEqual(state.events[0]["id"], 1)
        self.assertEqual(state.events[1]["type"], "response")
        # events_since
        after = state.events_since(1)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["content"], "hej")
        self.assertEqual(state.events_since(2), [])

    def test_serve_hitl_handler(self):
        import threading
        import time
        state = mod.ServeState(self._cfg(), MockHTTP())
        handler = mod._make_serve_hitl(state)

        def _answer():
            time.sleep(0.2)
            with state.lock:
                state.hitl_answer = "approved"
        threading.Thread(target=_answer, daemon=True).start()
        result = handler("request_hitl_approval", {"question": "Får jag?"})
        self.assertEqual(result, "approved")

    def test_serve_hitl_denied_on_timeout(self):
        state = mod.ServeState(self._cfg(), MockHTTP())
        state.cfg.timeout = 1  # kort timeout
        handler = mod._make_serve_hitl(state)
        result = handler("request_hitl_approval", {"question": "Får jag?"})
        self.assertEqual(result, "denied")  # timeout → avslå

    def test_request_abort(self):
        state = mod.ServeState(self._cfg(), MockHTTP())
        state.emit("status", status="running")
        self.assertTrue(state.request_abort())
        self.assertTrue(state.abort_flag)
        # is_busy false när ingen tråd kör
        self.assertFalse(state.is_busy())


class _FakeLoop:
    """Fångar anrop till AgentLoop.run och systemprompt-tilldelning."""

    def __init__(self, *a, **kw):
        self.system_prompt = kw.get("system_prompt", "")
        self.last_session_id = 42
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        return f"svar:{prompt}"


class TestReplAndSingleShot(unittest.TestCase):
    """Task 4.3: REPL-parse och single-shot."""

    def setUp(self):
        self._orig_loop = mod.AgentLoop
        self._orig_fetch = mod.fetch_skills
        self._orig_input = mod.input if hasattr(mod, "input") else None
        self.loops = []

        def _make_loop(*a, **kw):
            loop = _FakeLoop(*a, **kw)
            self.loops.append(loop)
            return loop

        mod.AgentLoop = _make_loop
        mod.fetch_skills = lambda http, names: [
            {"technical_name": n, "instruction": f"instruktion för {n}"} for n in names
        ]

    def tearDown(self):
        mod.AgentLoop = self._orig_loop
        mod.fetch_skills = self._orig_fetch

    def _feed_input(self, lines):
        """Returnerar en input()-ersättare som matar ut rader, sedan EOF."""
        it = iter(lines)

        def _inp(_prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        return _inp

    # ── REPL-parse ──

    def test_repl_quit_exits_zero(self):
        mod.input = self._feed_input(["/quit"])
        rc = mod.run_repl(_cfg(), MockHTTP())
        self.assertEqual(rc, 0)
        self.assertEqual(self.loops[0].prompts, [])  # inget uppdrag kört

    def test_repl_exit_alias(self):
        mod.input = self._feed_input(["/exit"])
        self.assertEqual(mod.run_repl(_cfg(), MockHTTP()), 0)

    def test_repl_eof_exits_zero(self):
        mod.input = self._feed_input([])  # direkt EOF
        self.assertEqual(mod.run_repl(_cfg(), MockHTTP()), 0)

    def test_repl_empty_line_skipped(self):
        mod.input = self._feed_input(["", "   ", "/quit"])
        mod.run_repl(_cfg(), MockHTTP())
        self.assertEqual(self.loops[0].prompts, [])  # tomma rader körs inte

    def test_repl_prompt_runs_loop(self):
        mod.input = self._feed_input(["hej", "/quit"])
        mod.run_repl(_cfg(), MockHTTP())
        self.assertEqual(self.loops[0].prompts, ["hej"])

    def test_repl_skills_command_reloads_prompt(self):
        mod.input = self._feed_input(["/skills saltstack,caddy", "/quit"])
        mod.run_repl(_cfg(), MockHTTP())
        # /skills är ett kommando — inte ett uppdrag
        self.assertEqual(self.loops[0].prompts, [])
        # systemprompten ska ha byggts om med de nya skillsen
        self.assertIn("saltstack", self.loops[0].system_prompt)
        self.assertIn("caddy", self.loops[0].system_prompt)

    def test_repl_skills_without_args(self):
        mod.input = self._feed_input(["/skills", "/quit"])
        mod.run_repl(_cfg(), MockHTTP())
        self.assertEqual(self.loops[0].prompts, [])

    def test_repl_loop_error_does_not_crash(self):
        """Ett fel i loopen ska fångas och REPL fortsätta."""
        def _boom(prompt):
            raise RuntimeError("nätverksfel")
        mod.input = self._feed_input(["krascha", "/quit"])
        mod.run_repl(_cfg(), MockHTTP())
        self.loops[0].run = _boom
        # kör om med trasig loop — ska ändå avsluta 0
        mod.input = self._feed_input(["krascha", "/quit"])
        self.assertEqual(mod.run_repl(_cfg(), MockHTTP()), 0)

    # ── Single-shot ──

    def test_single_shot_prints_answer(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mod.run_single_shot(_cfg(), MockHTTP(), "hej")
        self.assertEqual(rc, 0)
        self.assertIn("svar:hej", buf.getvalue())
        self.assertEqual(self.loops[0].prompts, ["hej"])

    def test_single_shot_json_output(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mod.run_single_shot(_cfg(json=True), MockHTTP(), "hej")
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["result"], "svar:hej")
        self.assertEqual(payload["session_id"], 42)

    def test_single_shot_error_returns_one(self):
        import io
        from contextlib import redirect_stdout
        loop = None

        def _make_loop(*a, **kw):
            nonlocal loop
            loop = _FakeLoop(*a, **kw)
            loop.run = lambda p: (_ for _ in ()).throw(RuntimeError("nej"))
            return loop
        mod.AgentLoop = _make_loop
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mod.run_single_shot(_cfg(), MockHTTP(), "hej")
        self.assertEqual(rc, 1)
        self.assertIn("nej", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
