# -*- coding: utf-8 -*-
"""Odoo-tester för decision-lineage (ai.lineage.link).

Körs med:
    odoo --config /etc/odoo/odoo.conf -d scalinq -u ai_agent_core \
         --test-enable --test-tags /ai_agent_core:TestLineage \
         --stop-after-init --workers 0

Bevisar:
- concept_injected-edge skapas vid OKF-injektion (med session i kontext)
- session_to_suggestion-edge skapas vid _create_suggestion
- suggestion_to_action-edge skapas vid action_accept/_materialize
- concept_evidence skapas vid skapande + write-spegling (källa → förslag)
- _get_lineage() bakåt ger full kedja till källa; framåt alla åtgärder
- edge-skapande kastar aldrig (try/except)
"""

from odoo.tests import TransactionCase


class TestLineage(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Lineage = self.env['ai.lineage.link']
        self.Sugg = self.env['workspace.activity.suggestion']
        # Testcoworker + session
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Lineage Test Coworker',
            'status': 'active',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'user_id': self.env.user.id,
            'name': 'Lineage test session',
        })
        # Testkoncept (company-scope, strategy-typ för injektion)
        strat = self.env['ai.artifact.type'].search(
            [('name', '=', 'strategy')], limit=1)
        self.concept = self.env['ai.okf.concept'].create({
            'scope': 'company',
            'owner_company_id': self.env.company.id,
            'artifact_type_id': strat.id or self.env['ai.artifact.type'].search(
                [], limit=1).id,
            'concept_key': 'lineage-test-concept',
            'summary': 'Testkoncept för lineage — strategisk inriktning',
            'status': 'stable',
        })

    def _edge_count(self, **domain):
        # search_count tar en domän-lista
        return self.Lineage.search_count([(k, '=', v) for k, v in domain.items()])

    # ── concept_injected ────────────────────────────────────────────────

    def test_concept_injected_edge_created(self):
        """Injektion med session i kontext skapar concept_injected-edge."""
        Okf = self.env['ai.okf.concept'].with_context(
            ai_lineage_session_id=self.session.id)
        block = Okf._okf_build_system_prompt_block(
            'company', self.env.company.id, query='lineage',
            include_level1=True, injection_level='summary_and_key')
        # Konceptet har strategy-typ → ska finnas i Level 3-blocket
        n = self._edge_count(
            kind='concept_injected',
            from_model='ai.coworker.session', from_id=self.session.id,
            to_model='ai.okf.concept', to_id=self.concept.id)
        self.assertGreaterEqual(n, 1,
                                'concept_injected-edge borde finnas '
                                '(injektion med session i kontext)')

    # ── session_to_suggestion + concept_evidence ────────────────────────

    def test_session_to_suggestion_edge_created(self):
        """_create_suggestion med session_id skapar session_to_suggestion."""
        sugg = self.Sugg._create_suggestion(
            'Testförslag', session_id=self.session.id,
            user=self.env.user, coworker_id=self.coworker.id)
        self.assertTrue(sugg.id)
        n = self._edge_count(
            kind='session_to_suggestion',
            from_model='ai.coworker.session', from_id=self.session.id,
            to_model='workspace.activity.suggestion', to_id=sugg.id)
        self.assertEqual(n, 1, 'session_to_suggestion-edge borde finnas')

    def test_concept_evidence_created_at_suggestion(self):
        """evidence_ids vid skapande skapar concept_evidence (källa → förslag)."""
        sugg = self.Sugg._create_suggestion(
            'Förslag med bevis', session_id=self.session.id,
            user=self.env.user, coworker_id=self.coworker.id,
            evidence_ids=[self.concept.id])
        n = self._edge_count(
            kind='concept_evidence',
            from_model='ai.okf.concept', from_id=self.concept.id,
            to_model='workspace.activity.suggestion', to_id=sugg.id)
        self.assertEqual(n, 1, 'concept_evidence-edge borde finnas')

    # ── suggestion_to_action ────────────────────────────────────────────

    def test_suggestion_to_action_edge_created(self):
        """action_accept (materialiserad) skapar suggestion_to_action-edge."""
        sugg = self.Sugg._create_suggestion(
            'Boka möte via lineage', suggestion_type='calendar.event',
            session_id=self.session.id, user=self.env.user,
            coworker_id=self.coworker.id)
        sugg.action_accept()
        self.assertTrue(sugg.result_ref, 'result_ref borde vara satt')
        model, _, rid = sugg.result_ref.partition(',')
        n = self._edge_count(
            kind='suggestion_to_action',
            from_model='workspace.activity.suggestion', from_id=sugg.id,
            to_model=model, to_id=int(rid))
        self.assertEqual(n, 1, 'suggestion_to_action-edge borde finnas')

    # ── write-spegling (framtida evidence) ──────────────────────────────

    def test_write_evidence_speglad(self):
        """Att lägga till evidence via write skapar concept_evidence."""
        sugg = self.Sugg._create_suggestion(
            'Förslag', session_id=self.session.id,
            user=self.env.user, coworker_id=self.coworker.id)
        sugg.write({'evidence_ids': [(4, self.concept.id, 0)]})
        n = self._edge_count(
            kind='concept_evidence',
            from_model='ai.okf.concept', from_id=self.concept.id,
            to_model='workspace.activity.suggestion', to_id=sugg.id)
        self.assertEqual(n, 1, 'write-spegling borde skapa edge')

    # ── _get_lineage ────────────────────────────────────────────────────

    def test_get_lineage_backward(self):
        """Bakåt: åtgärd → förslag → session + koncept (källa)."""
        sugg = self.Sugg._create_suggestion(
            'Boka möte', suggestion_type='calendar.event',
            session_id=self.session.id, user=self.env.user,
            coworker_id=self.coworker.id,
            evidence_ids=[self.concept.id])
        sugg.action_accept()
        model, _, rid = sugg.result_ref.partition(',')
        chain = self.Lineage.get_lineage_for_record(model, int(rid))
        kinds = {e['kind'] for e in chain}
        self.assertIn('suggestion_to_action', kinds)
        self.assertIn('session_to_suggestion', kinds)
        self.assertIn('concept_evidence', kinds,
                      'bakåt borde nå konceptet via concept_evidence')
        # Kedjan når konceptet
        to_refs = [e['to_ref'] for e in chain]
        self.assertTrue(any(f'workspace.activity.suggestion,{sugg.id}' == t
                            for t in to_refs))

    def test_get_lineage_forward(self):
        """Framåt: källa (koncept) → förslag → åtgärd."""
        sugg = self.Sugg._create_suggestion(
            'Boka möte', suggestion_type='calendar.event',
            session_id=self.session.id, user=self.env.user,
            coworker_id=self.coworker.id,
            evidence_ids=[self.concept.id])
        sugg.action_accept()
        chain = self.Lineage.get_lineage_for_record(
            'ai.okf.concept', self.concept.id, direction='forward')
        self.assertTrue(any(e['kind'] == 'concept_evidence' for e in chain),
                        'framåt borde inkludera concept_evidence till förslaget')
        self.assertTrue(any(e['kind'] == 'suggestion_to_action' for e in chain),
                        'framåt borde nå åtgärden via suggestion_to_action')

    # ── edge-skapande kastar aldrig ─────────────────────────────────────

    def test_edge_never_raises(self):
        """_add_edge med ogiltiga refs kastar inte (returnerar False)."""
        self.assertFalse(self.Lineage._add_edge('concept_evidence', '', ''))
        self.assertFalse(self.Lineage._add_edge('concept_evidence', 'x', 'y'))
        self.assertFalse(self.Lineage._add_edge('bogus_kind',
                                                'ai.okf.concept,1',
                                                'ai.okf.concept,2'))
        # ADD-only: unlink blockerad
        edge = self.Lineage._add_edge(
            'concept_evidence', 'ai.okf.concept,1', 'ai.okf.concept,2')
        if edge:
            edge.unlink()
            self.assertTrue(edge.exists(), 'unlink ska vara blockerad')
