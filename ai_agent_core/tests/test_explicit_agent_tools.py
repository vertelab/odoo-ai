# -*- coding: utf-8 -*-
"""Tester för explicit-agent-tools.

Körs med: checkmodule -d <db> -m ai_agent_core -t
Täcker:
- Seed-idempotens (builtin-verktyg → ai.tool-poster, inga duplikat)
- Konvertering med builtin_name → riktig handler + group_ids behålls
- ai.agent.create() med/utan explicita verktyg (settings-default)
- Settings-persistens (get/set_values rundtur för default_tool_ids)
- Kundchatt ser INTE interna verktyg utan explicit tool_ids
"""

from odoo.tests.common import TransactionCase

# Interna förmågor som ALDRIG ska hamna i en session utan explicit tool_ids.
INTERNAL_PREFIXES = (
    'describe_model', 'odoo_search', 'odoo_create', 'odoo_write',
    'odoo_unlink', 'odoo_call_method', 'inventory_', 'builder_',
    'nats_publish',
)
# Säkra kundvänliga verktyg (settings-default).
# Settings-default (DEFAULT_AGENT_TOOL_NAMES i res_config_settings.py).
# YouTube-verktygen lades till medvetet i commit 89e0ba8d ("youtube-verktyg
# i default-tools") — de är säkra kundverktyg (data-definierade, inga
# interna förmågor) och ska därför räknas som defaults. Listan här hade
# inte följt med, vilket gav ett falskt FAIL (FYND 2026-09-23).
SAFE_DEFAULTS = (
    'odoo_calculator', 'odoo_fetch_url', 'odoo_web_search',
    'youtube_get_transcript', 'youtube_search',
    'youtube_channel', 'youtube_playlist',
)


class TestBuiltinSeedIdempotens(TransactionCase):
    """Uppgift 8.1 — seedning körs två gånger utan duplikat."""

    def test_seed_twice_creates_no_duplicates(self):
        """Andra körningen skapar inga nya rader och ändrar inget."""
        Tool = self.env['ai.tool']
        Tool._ensure_builtin_tool_records()
        count_after_first = Tool.search_count([])
        names_after_first = {
            t.name: t.builtin_name for t in Tool.search([])
        }

        Tool._ensure_builtin_tool_records()
        count_after_second = Tool.search_count([])
        names_after_second = {
            t.name: t.builtin_name for t in Tool.search([])
        }

        self.assertEqual(
            count_after_first, count_after_second,
            'Seedningen ska vara idempotent — inga nya rader vid omkörning')
        self.assertEqual(
            names_after_first, names_after_second,
            'Ingen posts builtin_name får ändras vid omkörning')

    def test_seed_sets_builtin_name_and_binds_xmlid(self):
        """Seedade poster har builtin_name och en bunden xmlid."""
        Tool = self.env['ai.tool']
        Tool._ensure_builtin_tool_records()
        rec = Tool.search([('builtin_name', '=', 'odoo_search')], limit=1)
        self.assertTrue(rec, 'odoo_search ska finnas som seedad ai.tool-post')
        self.assertEqual(rec.builtin_name, 'odoo_search')

    def test_seed_does_not_overwrite_existing_bridge_record(self):
        """Befintlig post med samma namn får bara builtin_name satt.

        Använder en post vars namn kolliderar med ett builtin-verktyg men
        som INTE redan är seedad (annars finns den redan i DB och create()
        faller på ai_tool_name_unique).
        """
        Tool = self.env['ai.tool']
        # Använd ett builtin-namn och ta bort en ev. befintlig seedad post
        # först, så testet är självständigt (TransactionCase rullar tillbaka).
        candidate = 'odoo_calculator'
        Tool.search([('name', '=', candidate)]).unlink()

        # Skapa en "bridge-post" med anpassad beskrivning och NATS-subject.
        bridge = Tool.create({
            'name': candidate,
            'description': 'Min anpassade bridge-beskrivning',
            'executor': 'nats',
            'nats_subject': 'min.egen.subject',
        })
        Tool._ensure_builtin_tool_records()
        bridge.invalidate_recordset()
        self.assertEqual(
            bridge.description, 'Min anpassade bridge-beskrivning',
            'Seedningen får inte skriva över en befintlig posts beskrivning')
        self.assertEqual(
            bridge.nats_subject, 'min.egen.subject',
            'Seedningen får inte skriva över en anpassad NATS-subject')
        self.assertEqual(
            bridge.builtin_name, candidate,
            'Seedningen ska sätta builtin_name på den befintliga posten')


class TestBuiltinRuntimeConversion(TransactionCase):
    """Uppgift 8.2 — builtin_name → riktig handler + group_ids."""

    def setUp(self):
        super().setUp()
        self.env['ai.tool']._ensure_builtin_tool_records()

    def test_conversion_uses_real_handler_not_sandbox(self):
        """En post med builtin_name får den riktiga Python-handlern."""
        from odoo.addons.ai_agent_core.core.tools import (
            ai_tool_records_to_tools,
        )
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_search')], limit=1)
        self.assertTrue(rec, 'odoo_search måste finnas seedad')

        tools = ai_tool_records_to_tools(rec, self.env)
        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertIsNotNone(
            tool.handler, 'Builtin-verktyget ska ha en riktig handler')
        self.assertIn(
            'search', tool.handler.__name__,
            'Handlern ska vara den riktiga _tool_odoo_search, inte en sandbox')

    def test_conversion_keeps_group_ids(self):
        """group_ids från posten följer med till runtime-verktyget."""
        from odoo.addons.ai_agent_core.core.tools import (
            ai_tool_records_to_tools,
        )
        group = self.env['res.groups'].create({
            'name': 'Test Explicit Tools Group',
            'category_id': self.env.ref('base.module_category_hidden').id,
        })
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_search')], limit=1)
        rec.write({'group_ids': [(6, 0, group.ids)]})

        tools = ai_tool_records_to_tools(rec, self.env)
        self.assertEqual(
            tools[0].group_ids, group.ids,
            'group_ids ska bevaras så access-kontroll fungerar')

    def test_to_core_tool_uses_builtin_handler(self):
        """ai.tool.to_core_tool() ger samma riktiga handler."""
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_search')], limit=1)
        tool = rec.to_core_tool(env=self.env)
        self.assertIsNotNone(tool.handler)
        self.assertIn('search', tool.handler.__name__)


class TestDefaultToolIds(TransactionCase):
    """Uppgift 8.3 + 8.4 — create() med default + settings-persistens."""

    def test_new_agent_without_tools_gets_defaults(self):
        """Ny agent utan explicita verktyg får settings-default."""
        agent = self.env['ai.agent'].create({'name': 'Test Default Agent'})
        self.assertTrue(
            agent.tool_ids,
            'Ny agent utan tool_ids ska få settings-default-verktygen')

    def test_new_agent_with_explicit_tools_keeps_them(self):
        """Explicita verktyg respekteras — defaulten skrivs inte över."""
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_calculator')], limit=1)
        agent = self.env['ai.agent'].create({
            'name': 'Test Explicit Agent',
            'tool_ids': [(6, 0, rec.ids)],
        })
        self.assertEqual(
            agent.tool_ids.ids, rec.ids,
            'Explicita tool_ids ska bevaras orörda')

    def test_settings_roundtrip_persists_tool_names(self):
        """Settings get/set_values bevarar default-verktygen."""
        Settings = self.env['res.config.settings']
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_calculator')], limit=1)
        settings = Settings.create({
            'ai_default_tool_ids': [(6, 0, rec.ids)],
        })
        settings.set_values()

        values = Settings.get_values()
        self.assertIn(
            'ai_default_tool_ids', values,
            'get_values ska returnera ai_default_tool_ids')
        self.assertIn(
            rec.id, values['ai_default_tool_ids'][0][2],
            'Det sparade verktyget ska komma tillbaka i rundturen')


class TestNoImplicitBuiltins(TransactionCase):
    """Uppgift 8.5 — interna verktyg kräver explicit tool_ids."""

    def test_session_without_tools_has_no_internal_tools(self):
        """En session utan explicita verktyg exponerar inga interna."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Test Kund-Coworker (utan verktyg)',
        })
        tools, _groups = coworker._session_tools()
        names = [t.name for t in tools.list()]
        internal = [
            n for n in names if n.startswith(INTERNAL_PREFIXES)
        ]
        self.assertFalse(
            internal,
            'Interna verktyg läckte in i en session utan explicit tool_ids: '
            '%s' % internal)

    def test_session_exposes_only_settings_default_plus_explicit(self):
        """Sessionens verktyg = settings-default + explicita tool_ids."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Test Kund-Coworker (default)',
        })
        tools, _groups = coworker._session_tools()
        names = set(t.name for t in tools.list())
        # Alla exponerade verktyg ska vara säkra defaults (inga explicita).
        unexpected = names - set(SAFE_DEFAULTS)
        self.assertFalse(
            unexpected,
            'Sessionen exponerade verktyg utanför settings-default: %s'
            % unexpected)

    def test_coworker_with_explicit_odoo_tools_gets_them(self):
        """Motsatsen: explicita odoo-verktyg ger dem i sessionen."""
        rec = self.env['ai.tool'].search(
            [('builtin_name', '=', 'odoo_search')], limit=1)
        agent = self.env['ai.agent'].create({
            'name': 'Test Odoo Agent',
            'tool_ids': [(6, 0, rec.ids)],
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'Test Odoo-Coworker',
        })
        coworker.write({'agent_ids': [(0, 0, {'agent_id': agent.id})]})

        tools, _groups = coworker._session_tools()
        names = [t.name for t in tools.list()]
        self.assertIn(
            'odoo_search', names,
            'Explicit tool_ids ska ge verktyget i sessionen')
