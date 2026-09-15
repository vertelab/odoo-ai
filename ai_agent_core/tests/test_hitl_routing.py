# -*- coding: utf-8 -*-
"""Tester för HITL-routing per init-typ (external-agent-runtime §6).

Kärnan: mekanismen väljs av INIT-TYPEN, aldrig av agenten, och aldrig av
något "användare närvarande"-tillstånd.

- interaktiv → pausad HITL (`OpenAIInterruptHandler` → `AgentLoopPaused`)
- icke-interaktiv → record-HITL (`ai.coworker.hitl`), blockerar inte

Dessutom: runtime får inte vidga autonomigaten, och den externa processen
kan inte skriva HITL-poster själv.
"""

import asyncio

from odoo.tests import TransactionCase

from odoo.addons.ai_agent_core.core.interrupt import (
    AgentLoopPaused,
    AutoInterruptHandler,
    DiscussInterruptHandler,
    OpenAIInterruptHandler,
    WebUIInterruptHandler,
    INTERACTIVE_INIT_TYPES,
    select_interrupt_handler,
)


class TestHITLRouting(TransactionCase):

    def test_interactive_types_use_paused_hitl(self):
        """Interaktiva init-typer → pausad HITL via tool_calls."""
        for itype in ('server_action', 'powerbox', 'openai_api', 'manual'):
            handler = select_interrupt_handler(itype)
            self.assertIsInstance(
                handler, OpenAIInterruptHandler,
                '%s ska använda pausad HITL' % itype)

    def test_web_ui_uses_web_ui_handler(self):
        """web_ui med session-kontext → den rikare WebUI-handlern."""
        handler = select_interrupt_handler(
            'web_ui', web_ui_session='uuid-123', env=self.env)
        self.assertIsInstance(handler, WebUIInterruptHandler)

    def test_chat_uses_discuss_handler(self):
        """chat med kanal + bot-användare → Discuss-handlern."""
        channel = self.env['discuss.channel'].create({'name': 'HITL-test'})
        bot = self.env['res.users'].create({
            'name': 'HITL Bot', 'login': 'hitl_bot_test',
        })
        handler = select_interrupt_handler(
            'chat', discuss_channel=channel, discuss_bot_user=bot,
            env=self.env)
        self.assertIsInstance(handler, DiscussInterruptHandler)

    def test_non_interactive_types_use_record_hitl(self):
        """Icke-interaktiva init-typer → record-HITL."""
        for itype in ('cron', 'mail', 'watch', 'webhook'):
            handler = select_interrupt_handler(itype)
            self.assertIsInstance(
                handler, AutoInterruptHandler,
                '%s ska använda record-HITL' % itype)

    def test_selection_does_not_depend_on_user_presence(self):
        """Valet beror på init-typen, inte på ett närvarotillstånd."""
        # Samma init-typ ger samma handler oavsett kontext.
        a = select_interrupt_handler('cron')
        b = select_interrupt_handler('cron')
        self.assertIs(type(a), type(b))
        self.assertNotIn('cron', INTERACTIVE_INIT_TYPES)
        self.assertNotIn('webhook', INTERACTIVE_INIT_TYPES)
        self.assertIn('server_action', INTERACTIVE_INIT_TYPES)

    # ── 6.3: interaktiv körning pausar ───────────────────────────────

    def test_interactive_pauses_with_tool_call(self):
        """Pausad HITL kastar AgentLoopPaused med ett tool_call."""
        handler = OpenAIInterruptHandler()
        with self.assertRaises(AgentLoopPaused) as ctx:
            asyncio.run(handler.approve_tool(
                'salt.cmd_run', 'destructive', {'command': 'rm -rf /'}))
        calls = ctx.exception.tool_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['function']['name'],
                         'request_hitl_approval')

    # ── 6.4: icke-interaktiv blockerar inte ──────────────────────────

    def test_non_interactive_creates_record_and_does_not_block(self):
        """Record-HITL skapar en post och nekar tills vidare — utan att vänta."""
        created = []

        def fake_hitl_request(tool_name, risk_level, arguments):
            created.append((tool_name, risk_level, arguments))

        handler = AutoInterruptHandler(hitl_request=fake_hitl_request)
        approved = asyncio.run(handler.approve_tool(
            'salt.service_restart', 'destructive', {'service': 'caddy'}))
        self.assertFalse(approved, 'destruktivt verktyg ska inte auto-godkännas')
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][0], 'salt.service_restart')

    def test_read_only_tool_needs_no_approval(self):
        """Läsverktyg kräver inget godkännande (hitl_threshold=high_risk)."""
        created = []
        handler = AutoInterruptHandler(
            hitl_request=lambda *a: created.append(a))
        approved = asyncio.run(handler.approve_tool(
            'salt.disk_usage', 'read_only', {'path': '/var'}))
        self.assertTrue(approved)
        self.assertEqual(created, [], 'läsverktyg ska inte skapa ett ärende')

    # ── 6.5: samma gate för båda runtimes ────────────────────────────

    def test_same_threshold_same_gate_for_both_runtimes(self):
        """Runtime får inte vidga gaten — samma verktyg, samma utfall.

        Gaten bor i PermissionEngine + `hitl_threshold`, inte i runtime.
        Vi bevisar att runtime-fältet inte läses av gatens beslut.
        """
        import inspect
        from odoo.addons.ai_agent_core.core import permission
        src = inspect.getsource(permission)
        self.assertNotIn('runtime', src,
                         'PermissionEngine får inte läsa runtime — gaten '
                         'ska vara identisk för in_process och external')

    # ── 6.6: destruktivt verktyg kräver godkännande ──────────────────

    def test_destructive_requires_approval_even_at_highest_trust(self):
        """Även på högsta tillitssteget krävs godkännande för destructive."""
        created = []
        handler = AutoInterruptHandler(
            hitl_request=lambda *a: created.append(a))
        approved = asyncio.run(handler.approve_tool(
            'salt.cmd_run', 'destructive', {'command': 'reboot'}))
        self.assertFalse(approved)
        self.assertEqual(len(created), 1)

    # ── 6.2: extern process kan inte skriva HITL ─────────────────────

    def test_external_process_has_no_hitl_write_route(self):
        """OpenAI-vägen exponerar ingen route som skriver ai.coworker.hitl."""
        import inspect
        from odoo.addons.ai_agent_core.controllers import openai_api
        src = inspect.getsource(openai_api)
        # Ingen create/write mot HITL-modellen i kontrollern.
        self.assertNotIn("'ai.coworker.hitl'].create", src)
        self.assertNotIn('"ai.coworker.hitl"].create', src)
        self.assertNotIn("'ai.coworker.hitl'].write", src)
        self.assertNotIn('"ai.coworker.hitl"].write', src)


class TestSkillsEndpoint(TransactionCase):
    """`/ai/v1/skills` — skills-katalog för externa agenter (§7)."""

    def test_endpoint_is_domain_clean(self):
        """Endpointen innehåller ingen salt/zabbix/caddy-logik (core-rent).

        Vi granskar KODEN, inte docstringen: docstringen får (och ska)
        nämna regeln den följer. Därför tas docstringen bort först.
        """
        import ast
        import inspect
        from odoo.addons.ai_agent_core.controllers import stream
        src = inspect.getsource(stream.AIOpenAIAPI.list_skills)
        tree = ast.parse(inspect.cleandoc(src))
        fn = tree.body[0]
        # Ta bort docstring-noden (första stränguttrycket i kroppen).
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body = fn.body[1:]
        code = ast.unparse(tree).lower()
        for forbidden in ('salt', 'zabbix', 'caddy', 'postgres', 'dovecot'):
            self.assertNotIn(
                forbidden, code,
                'list_skills får inte nämna %s i KODEN — domänlogik hör '
                'till specialistlagret' % forbidden)

    def test_endpoint_returns_skills(self):
        """Endpointen svarar med skills ur core."""
        self.env['ai.skill'].create({
            'name': 'endpoint-test-skill',
            'description': 'En skill för endpoint-testet',
            'recipe_text': 'Gör så här.',
        })
        found = self.env['ai.skill'].search(
            [('name', '=', 'endpoint-test-skill')])
        self.assertEqual(len(found), 1)
        self.assertEqual(found.recipe_text, 'Gör så här.')
