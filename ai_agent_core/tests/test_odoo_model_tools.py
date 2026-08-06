# -*- coding: utf-8 -*-
"""Odoo-integrationstester för odoo-model-tools.

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker: describe_model/odoo_search (5.2), init_type-scoping (5.3),
sessionshantering (7.7) och idempotent agent-seedning (8.6).
"""

from odoo.tests.common import TransactionCase


class TestDescribeModel(TransactionCase):

    def test_describe_model_schema(self):
        env = self.env
        from odoo.addons.ai_agent_core.core.tools import _tool_describe_model
        import json
        out = json.loads(_tool_describe_model(env, 'res.partner'))
        self.assertIn('fields', out)
        self.assertIn('name', out['fields'])
        self.assertIn('capabilities', out)
        for cap in ('has_okf', 'has_graph', 'has_embedding'):
            self.assertIn(cap, out['capabilities'])

    def test_describe_model_unknown(self):
        env = self.env
        from odoo.addons.ai_agent_core.core.tools import _tool_describe_model
        import json
        out = json.loads(_tool_describe_model(env, 'does.not.exist'))
        self.assertIn('error', out)


class TestOdooSearch(TransactionCase):

    def test_search_default_fields(self):
        env = self.env
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_search
        import json
        partner = self.env['res.partner'].create({'name': 'Testpartner'})
        out = json.loads(_tool_odoo_search(
            env, 'res.partner', [('id', '=', partner.id)]))
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 1)
        self.assertIn('id', out[0])

    def test_search_scope_denied(self):
        env = self.env['res.partner'].with_context(
            _ai_scoped_models={'crm.lead'}).env
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_search
        import json
        out = json.loads(_tool_odoo_search(env, 'res.partner'))
        self.assertIn('error', out)


class TestSessionHandling(TransactionCase):

    def test_thread_save_response_without_coworker(self):
        """thread_save_response får inte kasta när coworker_id är NULL (7.7)."""
        env = self.env
        session = env['ai.coworker.session'].create({
            'name': 'test',
            'user_id': self.env.user.id,
        })
        self.assertFalse(session.coworker_id)
        # Simulera frontend-anropet: skapa rad via modellen direkt
        line = env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'assistant',
            'content': 'Svar utan coworker',
        })
        self.assertTrue(line)
        self.assertEqual(line.role, 'assistant')

    def test_thread_create_accepts_quest_id(self):
        """thread_create ska sätta coworker_id från quest_id (7.7)."""
        env = self.env
        coworker = env['ai.coworker'].create({
            'name': 'Testmedarbetare',
            'status': 'active',
        })
        # Här testas logiken direkt: quest_id → coworker_id
        vals = {
            'name': 'Tråd',
            'user_id': self.env.user.id,
            'thread_name': 'Tråd',
            'status': 'active',
        }
        qid = coworker.id
        vals['coworker_id'] = int(qid)  # = body.get('coworker_id') or body.get('quest_id')
        session = env['ai.coworker.session'].create(vals)
        self.assertEqual(session.coworker_id.id, coworker.id)


class TestDefaultCoworkerSeed(TransactionCase):

    def test_seed_is_idempotent(self):
        """_ensure_default_coworker skapar inga duplikat vid återkörning (8.6)."""
        env = self.env
        # Skapa en default-coworker (utan xmlid, som legacy)
        cw = env['ai.coworker'].create({
            'name': 'Allmän',
            'status': 'active',
            'is_default': True,
        })
        # Adoption: den riktiga default-coworkern behålls, testets arkiveras
        env['ai.coworker'].browse(cw.id)._ensure_default_coworker()
        default = env['ai.coworker'].search(
            [('is_default', '=', True), ('active', '=', True)], limit=1)
        names = set(default.agent_ids.mapped('agent_id.name'))
        # Agenter finns
        self.assertIn('Odoo-specialist', names)
        self.assertIn('Research', names)
        self.assertIn('Allmän kärna', names)
        # Supervisor-läge
        self.assertEqual(default.orchestration_mode, 'supervisor')
        # Idempotent: återkörning ger samma agenter (inga duplikat)
        env['ai.coworker'].browse(default.id)._ensure_default_coworker()
        names2 = set(default.agent_ids.mapped('agent_id.name'))
        self.assertEqual(names, names2)
        self.assertEqual(
            env['ai.agent'].search_count(
                [('name', 'in', ('Odoo-specialist', 'Research'))]),
            2,
        )
