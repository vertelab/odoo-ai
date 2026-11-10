# -*- coding: utf-8 -*-
"""Tester för injektionens semantik-val och memory_level (D7, D8).

VARFÖR DESSA TESTER FINNS: injektionen går i request-tråden. Ett synkront
embedding-anrop (20 s × 3 försök) kan blockera en worker i upp till 60 s per
scope — tre scope per tur ger 180 s. Mätt 2026-09-14: 2 av 6 anrop hängde.
Default ska därför vara BM25-only, och valet ska vara uttryckligt (config-
parameter), inte en tyst egenskap i koden.

`injection_level` togs bort samtidigt: `memory_level` (L0–L3) styr redan
injektionsnivån, och två fält som styr samma sak är sämre än ett.
"""

from unittest.mock import patch

from odoo.tests import common, tagged

from ._config_param_guard import ConfigParamGuardedCase


@tagged('okf', 'injection', 'post_install', '-at_install')
class TestInjectionSemantic(ConfigParamGuardedCase):
    """2.1 + 2.4: BM25 som default, memory_level som enda nivåstyrning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Coworker = cls.env['ai.coworker']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'knowledge')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'knowledge', 'kind': 'knowledge',
            })
        cls.scope = cls.env['ai.memory.scope'].search(
            [('code', '=', 'company')], limit=1)
        if not cls.scope:
            cls.scope = cls.env['ai.memory.scope'].create({
                'code': 'company', 'name': 'Företagets minne',
            })

    def _mk_concept(self, key, summary, query):
        """Skapa ett koncept med en unik term så träffen är vår.

        `source_ref` MÅSTE sättas: injektionen är fail-closed och avför
        koncept utan källhänvisning (`_format_concept_block`). Utan den
        blir testet grönt av fel anledning — tomt block, ingen träff.
        """
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, status, archived,
                 artifact_type_id, owner_company_id, source_ref,
                 create_date, write_date)
            VALUES (%s, %s, 'company', 'stable', false, %s, %s, %s,
                    now(), now())
            RETURNING id
        """, (key, summary + ' ' + query, self.atype.id,
              self.env.company.id,
              'res.company,%s' % self.env.company.id))
        return self.env.cr.fetchone()[0]

    def _coworker(self, level='L1'):
        return self.Coworker.create({
            'name': 'Injektionstest %s' % level,
            'memory_scopes': [(6, 0, self.scope.ids)],
            'memory_level': level,
            'context_injection_enabled': False,
            'use_time_context': False,
            'use_company_info': False,
            'graph_enrichment': False,
            'hybrid_search': True,
            'search_strategy': 'balanced',
        })

    def _set_semantic_param(self, value):
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.injection_semantic', value)
        self.env.registry.clear_cache()

    # ── 2.1: semantic=False gör inget HTTP-anrop ──

    def test_semantic_false_makes_no_embedding_call(self):
        """Default (False) ska aldrig röra `_embedding_post`."""
        self._set_semantic_param('False')
        self._mk_concept('inj.test', 'Kunden vill ha fakturor via e-post.',
                         'injektionsterm')
        coworker = self._coworker()

        import traceback
        Provider = self.env['ai.provider']
        _stacks = []

        def _boom(self, *a, **kw):
            _stacks.append(''.join(traceback.format_stack()))
            raise AssertionError('embedding-anrop trots semantic=False')

        with patch.object(type(Provider), '_embedding_post', _boom):
            block = coworker._build_injection_prompt(
                user=self.env.user, prompt='injektionsterm')

        self.assertFalse(_stacks, 'embedding anropades:\n' + '\n'.join(_stacks))
        self.assertIn('injektionsterm', block,
                      'BM25 ska fortfarande ge en träff')

    def test_semantic_true_does_call_the_embedding_provider(self):
        """När parametern slås på ska vägen gå via providern.

        Vi mockar `_get_embedding` (inte `_embedding_post`) — det är
        anropspunkten `_okf_search` faktiskt använder, och vi vill bevisa
        att den nås, inte att HTTP-lagret fungerar.
        """
        self._set_semantic_param('True')
        self._mk_concept('inj.sem', 'Kunden vill ha fakturor via e-post.',
                         'semantiskterm')
        coworker = self._coworker()

        Provider = self.env['ai.provider']
        calls = []

        def _fake_get_embedding(self, input=None, input_type=None):
            calls.append(input)
            return [0.1] * 1024

        with patch.object(type(Provider), '_embedding_provider',
                          return_value=Provider.search([], limit=1) or
                          Provider.new({'name': 'stub'})), \
                patch.object(type(Provider), '_get_embedding',
                             _fake_get_embedding):
            coworker._build_injection_prompt(
                user=self.env.user, prompt='semantiskterm')

        self.assertTrue(calls, 'semantic=True ska anropa providern')

    # ── 2.4: memory_level styr injektionsnivån ──

    def test_l0_has_no_level1_block(self):
        """L0 → summary_only: L1-blocket (RELEVANT KUNSKAP) ska vara tomt."""
        self._set_semantic_param('False')
        self._mk_concept('inj.l0', 'Kunden vill ha fakturor via e-post.',
                         'nollnivåterm')
        coworker = self._coworker(level='L0')

        block = coworker._build_injection_prompt(
            user=self.env.user, prompt='nollnivåterm')

        self.assertNotIn('RELEVANT KUNSKAP', block,
                         'L0 får inte injicera L1-blocket')
        # Konceptet får fortfarande synas i L2/L3 (summary_only är L2+L3) —
        # det är bara L1-sökningen som ska vara avstängd.

    def test_l1_has_level1_block(self):
        """L1 → summary_and_key: L1-blocket ska finnas."""
        self._set_semantic_param('False')
        self._mk_concept('inj.l1', 'Kunden vill ha fakturor via e-post.',
                         'ettnivåterm')
        coworker = self._coworker(level='L1')

        block = coworker._build_injection_prompt(
            user=self.env.user, prompt='ettnivåterm')

        self.assertIn('RELEVANT KUNSKAP', block,
                      'L1 ska injicera L1-blocket')
        self.assertIn('ettnivåterm', block,
                      'konceptet ska finnas i L1-blocket')
