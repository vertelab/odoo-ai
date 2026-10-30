# -*- coding: utf-8 -*-
"""Tester för ai.memory med OKF-formatet (ai-memory-okf-format T5).

VARFÖR: `ai.okf.mixin` bar två saker som såg ut som en — FORMATET
(okf_body, okf_summary, okf_tags, okf_links) och INDEXERINGEN (okf_dirty,
okf_indexed_at + hookar). `memory`-specen förbjuder den andra gruppen på
`ai.memory`, men den första är precis vad en markdown-fil är.

Dessa tester bevisar:
  1. ai.memory bär formatet men INTE flaggorna (T5.1)
  2. ett minne kan höra till flera agenter (T5.2)
  3. avkoppling raderar inte minnet (T5.3)
  4. `content` är ett fungerande alias för `okf_body` (T5.4)
  5. personal/company-minnena är oförändrade (T5.5)
"""

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestAiMemoryOkfFormat(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mem = cls.env['ai.memory']
        cls.Agent = cls.env['ai.agent']
        cls.Link = cls.env['ai.agent.memory']

    # ── T5.1: formatet finns, flaggorna inte ─────────────────────────

    def test_memory_has_format_fields(self):
        """ai.memory ska bära OKF-formatets fyra fält."""
        for f in ('okf_body', 'okf_summary', 'okf_links', 'okf_tags'):
            self.assertIn(
                f, self.Mem._fields,
                'ai.memory saknar OKF-formatfältet %r' % f)

    def test_memory_has_no_indexing_flags(self):
        """Flaggorna ska vara OMÖJLIGA — fältet ska inte finnas."""
        for f in ('okf_dirty', 'okf_indexed_at', 'artifact_type_id'):
            self.assertNotIn(
                f, self.Mem._fields,
                'ai.memory får inte bära %r (memory-specen: artefakten är '
                'material, inte inlärning)' % f)

    def test_memory_inherits_format_not_index_mixin(self):
        """Arvet ska vara format-mixinen, inte ai.okf.mixin."""
        self.assertIn('ai.okf.format.mixin', self.Mem._inherit)
        self.assertNotIn(
            'ai.okf.mixin', self.Mem._inherit,
            'ai.memory får inte ärva ai.okf.mixin — den bär flaggorna')

    def test_personal_and_company_keep_flags(self):
        """Regression: personal/company-minnena ska behålla flaggorna."""
        for model in ('ai.personal.memory', 'ai.company.memory'):
            M = self.env[model]
            for f in ('okf_body', 'okf_summary', 'okf_dirty',
                      'okf_indexed_at'):
                self.assertIn(
                    f, M._fields,
                    '%s ska fortfarande ha %r' % (model, f))

    # ── T5.2: ett minne, flera agenter ───────────────────────────────

    def test_memory_belongs_to_multiple_agents(self):
        """Samma artefakt ska kunna kopplas till flera agenter."""
        agents = self.Agent.search([], limit=2)
        if len(agents) < 2:
            self.skipTest('behöver minst två ai.agent')

        mem = self.Mem.create({
            'name': 'delad_handbok',
            'memory_type': 'pdf',
            'okf_body': 'Handbok som flera agenter ska kunna använda.',
        })
        self.Link.create({
            'agent_id': agents[0].id, 'memory_id': mem.id, 'sequence': 10})
        self.Link.create({
            'agent_id': agents[1].id, 'memory_id': mem.id, 'sequence': 10})

        self.assertIn(
            mem, agents[0].memory_ids.mapped('memory_id'))
        self.assertIn(
            mem, agents[1].memory_ids.mapped('memory_id'))
        # En enda artefakt bär innehållet.
        self.assertEqual(
            self.Mem.search_count([('name', '=', 'delad_handbok')]), 1)

    def test_link_is_unique_per_agent_and_memory(self):
        """Samma koppling får inte skapas två gånger."""
        agent = self.Agent.search([], limit=1)
        if not agent:
            self.skipTest('ingen ai.agent')
        mem = self.Mem.create({'name': 'unika_koppling'})
        self.Link.create({'agent_id': agent.id, 'memory_id': mem.id})
        from psycopg2 import IntegrityError
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Link.create({'agent_id': agent.id, 'memory_id': mem.id})

    # ── T5.3: avkoppling raderar inte ────────────────────────────────

    def test_deactivating_link_keeps_memory(self):
        """active=False ska arkivera kopplingen, inte radera minnet."""
        agents = self.Agent.search([], limit=2)
        if len(agents) < 2:
            self.skipTest('behöver minst två ai.agent')
        mem = self.Mem.create({'name': 'avkoppling_test'})
        l1 = self.Link.create({'agent_id': agents[0].id, 'memory_id': mem.id})
        self.Link.create({'agent_id': agents[1].id, 'memory_id': mem.id})

        l1.write({'active': False})

        self.assertTrue(mem.exists(), 'minnet ska finnas kvar')
        self.assertFalse(l1.active)
        # Den andra agenten ser fortfarande minnet.
        other = self.Link.search([
            ('agent_id', '=', agents[1].id),
            ('memory_id', '=', mem.id),
        ])
        self.assertTrue(other.active)

    def test_expired_link_is_not_current(self):
        """En koppling utanför giltighetsfönstret ska inte injiceras."""
        agent = self.Agent.search([], limit=1)
        if not agent:
            self.skipTest('ingen ai.agent')
        mem = self.Mem.create({'name': 'utgangen'})
        link = self.Link.create({
            'agent_id': agent.id, 'memory_id': mem.id,
            'expires_at': '2020-01-01',
        })
        self.assertFalse(link._is_current())
        link.write({'expires_at': False, 'not_before': '2020-01-01'})
        self.assertTrue(link._is_current())

    def test_role_defaults_to_knowledge(self):
        """Rollen ska defaulta till knowledge, inte vara tom."""
        agent = self.Agent.search([], limit=1)
        if not agent:
            self.skipTest('ingen ai.agent')
        mem = self.Mem.create({'name': 'roll_test'})
        link = self.Link.create({'agent_id': agent.id, 'memory_id': mem.id})
        self.assertEqual(link.role, 'knowledge')

    # ── T5.4: content är ett alias ───────────────────────────────────

    def test_content_is_alias_for_okf_body(self):
        """content och okf_body ska vara samma text."""
        mem = self.Mem.create({
            'name': 'alias_test',
            'okf_body': 'Texten bor i okf_body.',
        })
        self.assertEqual(mem.content, 'Texten bor i okf_body.')

        mem.content = 'Skrivet via content.'
        self.assertEqual(mem.okf_body, 'Skrivet via content.')

        mem.okf_body = 'Skrivet via okf_body.'
        self.assertEqual(mem.content, 'Skrivet via okf_body.')

    def test_search_on_content_searches_okf_body(self):
        """En domän på content ska söka i okf_body."""
        mem = self.Mem.create({
            'name': 'sok_test',
            'okf_body': 'Unikt sökord: kvartalsavstämning.',
        })
        found = self.Mem.search([('content', 'ilike', 'kvartalsavstämning')])
        self.assertIn(mem, found)

    def test_content_column_is_gone(self):
        """content-kolumnen ska vara droppad — fältet är compute nu."""
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_memory' AND column_name = 'content'
        """)
        self.assertFalse(
            self.env.cr.fetchone(),
            'ai_memory.content ska vara droppad (migration 18.0.2.0.22)')

    # ── RAG-kapaciteten är orörd ─────────────────────────────────────

    def test_rag_search_uses_okf_body(self):
        """_memory_body_text ska läsa okf_body, inte content (cirkel)."""
        mem = self.Mem.create({
            'name': 'rag_test',
            'okf_body': 'Avtalshantering och förnyelse.',
        })
        self.assertEqual(
            mem._memory_body_text(), 'Avtalshantering och förnyelse.')
