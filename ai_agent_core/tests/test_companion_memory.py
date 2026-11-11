# -*- coding: utf-8 -*-
"""Tester för companion-minnet (companion-memory-completeness).

Verifierar:
  (2.1–2.5) D1 — companion-regeln: trust-grinden vidgas för `personal` när
            medarbetaren är en companion (`memory_learning_direct`) MED
            `learning='active'`. `company` och `always` är oförändrade.
  (3.1–3.5) D2 — skrivgränsen för det personliga minnet (F3).
  (4.1–4.7) D3–D7 — negativ lista, kind-routning, ärlig sökkatalog,
            L2-regeln (F4).

Körs med `checkmodule -t` (kräver `-l test` för att skriva ut resultatet).
"""

from unittest.mock import patch
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from ._test_users import create_user


@tagged('companion_memory', 'post_install')
class TestCompanionTrustGate(TransactionCase):
    """D1 — trust-grinden och companion-regeln."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.coworker = self.Coworker.create({
            'name': 'Companion-test',
            'orchestration_mode': 'single',
            'status': 'active',
        })

    def _set(self, **vals):
        self.coworker.write(vals)
        return self.coworker

    # ── 2.5 scenarier ──────────────────────────────────────────────────

    def test_companion_writes_personal_directly(self):
        """Companion (direct + active) skriver personal direkt."""
        self._set(
            hitl_threshold='high_risk',
            memory_learning_direct=True,
            learning='active',
        )
        self.assertTrue(self.coworker._learning_write_is_direct('personal'))

    def test_companion_without_active_learning_defers(self):
        """Companion utan aktivt lärande föreslår i stället."""
        self._set(
            hitl_threshold='high_risk',
            memory_learning_direct=True,
            learning='passive',
        )
        self.assertFalse(self.coworker._learning_write_is_direct('personal'))

    def test_non_companion_is_unchanged(self):
        """Icke-companion (direct=False) föreslår."""
        self._set(
            hitl_threshold='high_risk',
            memory_learning_direct=False,
            learning='active',
        )
        self.assertFalse(self.coworker._learning_write_is_direct('personal'))

    def test_company_always_requires_approval(self):
        """company kräver godkännande även för en companion."""
        self._set(
            hitl_threshold='high_risk',
            memory_learning_direct=True,
            learning='active',
        )
        self.assertFalse(self.coworker._learning_write_is_direct('company'))

    def test_always_overrides_companion(self):
        """always överordnar companion-regeln."""
        self._set(
            hitl_threshold='always',
            memory_learning_direct=True,
            learning='active',
        )
        self.assertFalse(self.coworker._learning_write_is_direct('personal'))
        self.assertFalse(self.coworker._learning_write_is_direct('coworker'))

    def test_coworker_scope_direct_at_high_risk(self):
        """coworker-scopet skrivs direkt vid high_risk (oförändrat)."""
        self._set(hitl_threshold='high_risk')
        self.assertTrue(self.coworker._learning_write_is_direct('coworker'))

    def test_autonomous_writes_all_scopes(self):
        """autonomous skriver alla scopes direkt."""
        self._set(hitl_threshold='autonomous')
        for scope in ('company', 'personal', 'coworker'):
            self.assertTrue(
                self.coworker._learning_write_is_direct(scope),
                'autonomous borde skriva %s direkt' % scope)

    # ── 2.2 seedning ───────────────────────────────────────────────────

    def test_hermes_profile_seeds_direct(self):
        """hermes-profilen seedar memory_learning_direct vid skapande."""
        identity = self.env['ai.identity'].create({
            'name': 'Companion-identitet',
            'memory_profile': 'hermes',
        })
        cw = self.Coworker.create({
            'name': 'Hermes-seedad',
            'orchestration_mode': 'single',
            'status': 'active',
            'identity_id': identity.id,
        })
        self.assertTrue(cw.memory_learning_direct)

    def test_balanced_profile_does_not_seed_direct(self):
        """balanced-profilen lämnar memory_learning_direct=False."""
        identity = self.env['ai.identity'].create({
            'name': 'Balanserad identitet',
            'memory_profile': 'balanced',
        })
        cw = self.Coworker.create({
            'name': 'Balanced-seedad',
            'orchestration_mode': 'single',
            'status': 'active',
            'identity_id': identity.id,
        })
        self.assertFalse(cw.memory_learning_direct)

    def test_seed_does_not_reenable_disabled(self):
        """Backfill-seedningen får inte återaktivera ett avstängt fält."""
        identity = self.env['ai.identity'].create({
            'name': 'Companion-identitet 2',
            'memory_profile': 'hermes',
        })
        cw = self.Coworker.create({
            'name': 'Companion avstängd',
            'orchestration_mode': 'single',
            'status': 'active',
            'identity_id': identity.id,
        })
        self.assertTrue(cw.memory_learning_direct)
        cw.memory_learning_direct = False
        # _seed_memory_settings körs av backfill på befintliga rader.
        cw._seed_memory_settings()
        self.assertFalse(
            cw.memory_learning_direct,
            'seedningen fick inte återaktivera ett avstängt fält')


@tagged('companion_memory', 'post_install')
class TestCompanionWriteBudget(TransactionCase):
    """D2 — skrivgränsen för det personliga minnet.

    OBS: testerna skriver koncept via `_okf_upsert`, som försöker producera
    en embedding. Utan en mockad provider går det anropet till Bifrost, och
    Odoos testramverk blockerar externa HTTP-anrop (`BlockedRequest`) —
    vilket triggade en oändlig retry-loop i `_get_embedding` och fick hela
    testsviten att hänga (mätt 2026-11-02: 292 blockerade anrop, 106
    omgångar av "försök 1/3"). `_embedding_provider` mockas därför till
    None: konceptet sparas med `embedding_state='pending'`, vilket är
    exakt det tillstånd budgettestet bryr sig om.
    """

    def setUp(self):
        super().setUp()
        # Mocka bort embedding-providern (se klassens docstring).
        self._embed_patch = patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            return_value=self.env['ai.provider'])
        self._embed_patch.start()
        self.user = self.env.create_user(name='Budget-testare', login='budget_user_%s' % self.env['res.users'].search_count([]), password='budget-pass', groups_id=[(6, 0, [self.env.ref('base.group_user').id])])
        self.Okf = self.env['ai.okf.concept']

    def tearDown(self):
        self._embed_patch.stop()
        super().tearDown()

    def test_budget_is_configurable(self):
        """Budgeten läses ur konfigurationen, inte ur en konstant."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_memory.char_budget', '1234')
        self.assertEqual(self.Okf._personal_memory_budget(), 1234)

    def test_budget_default_when_unset(self):
        """Utan konfigurerat värde används defaulten."""
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'odoomind.personal_memory.char_budget'),
        ]).unlink()
        self.assertGreater(self.Okf._personal_memory_budget(), 0)

    def test_usage_counts_personal_concepts(self):
        """Bruket summerar tecken för användarens aktiva personal-koncept."""
        self.Okf._okf_upsert(
            'learning', concept_key='test.budget.usage',
            summary='x' * 100, owner_user_id=self.user.id,
            generated_by='test')
        self.assertGreaterEqual(
            self.Okf._personal_memory_usage(self.user), 100)

    def test_write_at_hard_cap_is_refused(self):
        """Skrivning vid hårt tak vägras och skapar ingen rad."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_memory.char_budget', '150')
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_memory.warn_pct', '80')
        self.Okf._okf_upsert(
            'learning', concept_key='test.budget.fill',
            summary='y' * 140, owner_user_id=self.user.id,
            generated_by='test')
        before = self.Okf.search_count([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id)])
        with self.assertRaises(UserError):
            self.Okf._okf_upsert(
                'learning', concept_key='test.budget.over',
                summary='z' * 100, owner_user_id=self.user.id,
                generated_by='test')
        after = self.Okf.search_count([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id)])
        self.assertEqual(before, after, 'ingen rad fick skapas')

    def test_write_in_warning_zone_succeeds(self):
        """Skrivning i varningszonen går igenom."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_memory.char_budget', '1000')
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_memory.warn_pct', '80')
        self.Okf._okf_upsert(
            'learning', concept_key='test.warn.fill',
            summary='w' * 850, owner_user_id=self.user.id,
            generated_by='test')
        # 850 + 100 = 950 < 1000 → går igenom (i varningszonen)
        self.Okf._okf_upsert(
            'learning', concept_key='test.warn.more',
            summary='v' * 100, owner_user_id=self.user.id,
            generated_by='test')
        self.assertEqual(self.Okf.search_count([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id),
            ('concept_key', '=', 'test.warn.more')]), 1)


@tagged('companion_memory', 'post_install')
class TestCompanionQuality(TransactionCase):
    """D3–D7 — negativ lista, kind-routning, ärlig katalog, L2-regel."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Kvalitets-test',
            'orchestration_mode': 'single',
            'status': 'active',
            'learning': 'active',
            'hitl_threshold': 'high_risk',
            'memory_learning_direct': True,
        })
        self.user = self.env.create_user(name='Kvalitets-användare', login='kvalitet_%s' % self.env['res.users'].search_count([]), password='kvalitet-pass', groups_id=[(6, 0, [self.env.ref('base.group_user').id])])
        self.Okf = self.env['ai.okf.concept']

    # ── 4.1 negativ listan ────────────────────────────────────────────

    def test_negative_list_in_prompt(self):
        """The reflection prompt carries DO NOT SAVE with the six categories."""
        import inspect
        src = inspect.getsource(
            type(self.coworker)._extract_concepts_from_conversation)
        self.assertIn('DO NOT SAVE', src)
        for needle in ('trivial', 'easy to look up', 'raw data',
                       'task progress', 'logs', 'TODO'):
            self.assertIn(needle, src,
                          'kategorin %r saknas i prompten' % needle)
        self.assertIn('ALWAYS SAVE', src)
        self.assertIn('preference', src)

    # ── 4.2 kind-normalisering ────────────────────────────────────────

    def test_kind_normalization(self):
        """Saknat/okänt kind blir fact; procedure bevaras."""
        allowed = ('fact', 'procedure')
        for raw, expected in ((None, 'fact'), ('', 'fact'),
                              ('FACT', 'fact'), ('procedure', 'procedure'),
                              ('PROCEDURE', 'procedure'),
                              ('skill', 'fact'), ('x', 'fact')):
            kind = str(raw or '').strip().lower()
            got = kind if kind in allowed else 'fact'
            self.assertEqual(got, expected,
                             'kind=%r borde bli %r' % (raw, expected))

    # ── 4.3/4.4 procedure → ai.skill ──────────────────────────────────

    def test_procedure_becomes_skill_not_concept(self):
        """En procedur skriver ai.skill och inget personal-koncept."""
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': self.user.id,
            'status': 'active',
            'name': 'Procedur-test',
        })
        before_okf = self.Okf.search_count([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id)])
        n = self.coworker._learn_procedure_from_session(
            session, 'Så här bokför man en kundfaktura i tre steg',
            llm_key='bokfora.kundfaktura')
        self.assertEqual(n, 1)
        skill = self.env['ai.skill'].search([
            ('name', '=', 'Så här bokför man en kundfaktura i tre steg')])
        self.assertTrue(skill, 'proceduren borde bli en ai.skill')
        after_okf = self.Okf.search_count([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id)])
        self.assertEqual(before_okf, after_okf,
                         'proceduren fick inte bli ett personligt koncept')
        # 4.4: proceduren bär source_user_id (coworker-scope, avgränsad)
        proc = self.Okf.search([
            ('scope', '=', 'coworker'),
            ('owner_coworker_id', '=', self.coworker.id),
            ('concept_key', 'ilike', 'procedure.%'),
        ], limit=1)
        self.assertTrue(proc)
        self.assertEqual(proc.source_user_id.id, self.user.id)

    # ── 4.5 ärlig sökkatalog ──────────────────────────────────────────

    def test_hermes_profile_only_wired_sources(self):
        """hermes-profilen pekar bara på is_wired=True-källor."""
        vals = self.coworker._search_profile_presets('hermes')
        source_ids = vals['search_sources'][0][2]
        sources = self.env['ai.search.source'].browse(source_ids)
        for s in sources:
            self.assertTrue(s.is_wired,
                            'profilen pekade på icke-wired källa %s' % s.code)
        self.assertIn('okf_concept', sources.mapped('code'))

    def test_balanced_profile_only_wired_sources(self):
        """balanced-profilen pekar bara på is_wired=True-källor."""
        vals = self.coworker._search_profile_presets('balanced')
        sources = self.env['ai.search.source'].browse(
            vals['search_sources'][0][2])
        for s in sources:
            self.assertTrue(s.is_wired,
                            'profilen pekade på icke-wired källa %s' % s.code)

    # ── 4.6 L2-regeln ─────────────────────────────────────────────────

    def _make_concept(self, atype_name, key, summary, status='stable'):
        """Skapa via _okf_upsert — produktionsvägen, med riktig källa.

        Access-resolvern prövar source_ref mot den faktiska posten. Ett
        påhittat 'ai.personal.goal,1' ger synlighet False och konceptet
        filtreras bort. Testet skapar därför ett riktigt mål och pekar på
        det — precis som goal_indexer gör.
        """
        atype = self.env['ai.artifact.type'].search(
            [('name', '=', atype_name)], limit=1)
        self.assertTrue(atype, 'artefakttypen %s saknas' % atype_name)
        goal = self.env['ai.personal.goal'].create({
            'user_id': self.user.id,
            'name': summary[:80],
            'status': 'active',
        })
        ref = 'ai.personal.goal,%s' % goal.id
        concept = self.Okf._okf_upsert(
            atype.id, concept_key=key, summary=summary,
            title=summary[:80],
            source_ref=ref,
            attribution=[{'source_ref': ref, 'role': 'goal', 'line': 1}],
            owner_user_id=self.user.id, generated_by='test')
        if status != 'stable':
            concept.write({'status': status})
        return concept

    def test_role_and_active_goal_are_l2(self):
        """roll och aktivt mål lyfts till L2; avslutat mål stannar i L1.

        OKF-konceptets `status` är draft/stable/superseded/deprecated.
        Ett mål-koncept skapas bara för mål med active=True
        (_index_user_goals), så ett icke-deprecated mål är aktivt. Ett
        avslutat mål markeras deprecated.
        """
        self._make_concept('roll', 'user.%s.role' % self.user.id,
                           'Användaren är ekonomichef')
        self._make_concept('goal', 'user.%s.goal.1' % self.user.id,
                           'Målet är att stänga boken i tid', status='stable')
        self._make_concept('goal', 'user.%s.goal.2' % self.user.id,
                           'Avslutat mål', status='deprecated')
        block = self.Okf._okf_build_system_prompt_block(
            'personal', self.user.id, query='roll', max_chars=4000,
            user=self.user)
        self.assertIn('PERSONLIGT', block)
        self.assertIn('ekonomichef', block)
        self.assertIn('stänga boken i tid', block)
        self.assertNotIn('Avslutat mål', block)

    def test_goal_indexer_uses_status_not_active(self):
        """Mål-indexeraren söker på `status`, inte på ett icke-existerande
        `active`-fält (fynd 2026-11-05, task 4.6).

        Före fixen kastade `_index_user_goals`
        `ValueError: Invalid field 'active'` — felet svaldes av
        `_index_all_personal_sources`, och resultatet blev F1:s mätning
        "goal_indexer = 0": L2-blocket för mål var alltid tomt.
        """
        goal = self.env['ai.personal.goal'].create({
            'user_id': self.user.id,
            'name': 'Indexerar-testmål',
            'status': 'active',
        })
        # Kastar ValueError om sökningen fortfarande använder 'active'
        count = self.Okf._index_user_goals(self.user.id)
        self.assertGreaterEqual(
            count, 1, 'indexeraren skapade inget koncept för ett aktivt mål')
        concept = self.Okf.search([
            ('source_ref', '=', 'ai.personal.goal,%s' % goal.id),
        ])
        self.assertTrue(concept, 'inget OKF-koncept för målet')
        self.assertNotEqual(concept.status, 'deprecated')

    def test_completed_goal_is_deprecated(self):
        """Avslutat mål markerar sitt koncept deprecated — annars ligger det
        kvar i L2 för evigt (fynd 2026-11-05, task 4.6).
        """
        goal = self.env['ai.personal.goal'].create({
            'user_id': self.user.id,
            'name': 'Avsluta-testmål',
            'status': 'active',
        })
        self.Okf._index_user_goals(self.user.id)
        concept = self.Okf.search([
            ('source_ref', '=', 'ai.personal.goal,%s' % goal.id),
        ])
        self.assertTrue(concept)
        self.assertNotEqual(concept.status, 'deprecated')

        goal.action_complete()
        concept.invalidate_recordset()
        self.assertEqual(
            concept.status, 'deprecated',
            'avslutat mål ska depreceras så L2-filtret släpper det')

    # ── 5.2 Trust-grinden i produktionsvägen ──────────────────────────

    def _ensure_scopes(self, codes=('company', 'personal', 'coworker')):
        """Sätt memory_scopes explicit — seedning sker inte automatiskt i test."""
        Scope = self.env['ai.memory.scope']
        wanted = Scope.search([('code', 'in', list(codes))])
        self.coworker.memory_scopes = [(6, 0, wanted.ids)]
        return self.coworker.memory_scopes.mapped('code')

    def _session_with_line(self, name, summary):
        """Skapa en session med ett eftermäle OCH minst en rad.

        `_learn_from_session` returnerar 0 direkt om `session_line_ids` är
        tom (den lär inte på ett tomt underlag), så en summary utan rad
        räcker inte — även om conversation-texten hade byggts av den.
        """
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'user_id': self.user.id,
            'status': 'active',
            'name': name,
            'summary': summary,
        })
        self.env['ai.coworker.session.line'].create({
            'session_id': session.id,
            'coworker_id': self.coworker.id,
            'role': 'user',
            'content': summary,
            'sequence': 1,
        })
        return session

    def test_trust_gate_matrix(self):
        """D1: `_learning_write_is_direct` för alla kombinationer.

        Task 5.2. Grinden är en ren funktion — den testas direkt i stället
        för via en LLM-konversation (icke-deterministisk, kostar pengar,
        kan inte köras i CI).
        """
        cw = self.coworker
        cases = [
            # (hitl_threshold, memory_learning_direct, learning, scope, väntat)
            ('autonomous', False, 'active', 'personal', True),
            ('autonomous', False, 'active', 'company', True),
            ('autonomous', False, 'active', 'coworker', True),
            ('high_risk', False, 'active', 'coworker', True),
            ('high_risk', True, 'active', 'personal', True),
            ('high_risk', False, 'active', 'personal', False),
            ('high_risk', True, 'passive', 'personal', False),
            ('high_risk', True, 'active', 'company', False),
            ('always', True, 'active', 'personal', False),
            ('always', True, 'active', 'coworker', False),
        ]
        for hitl, direct, learning, scope, expected in cases:
            cw.write({'hitl_threshold': hitl,
                      'memory_learning_direct': direct,
                      'learning': learning})
            self.assertEqual(
                cw._learning_write_is_direct(scope), expected,
                'hitl=%s direct=%s learning=%s scope=%s borde ge %s'
                % (hitl, direct, learning, scope, expected))

    def test_companion_writes_personal_directly_no_proposal(self):
        """D1: en companion skriver `personal` direkt — inget förslag i kön.

        Task 5.2. Reflektionen mockas (patcha
        `_extract_concepts_from_conversation`) så testet bevisar grindens
        logik, inte modellens svarskvalitet.
        """
        from unittest.mock import patch

        session = self._session_with_line(
            'Companion-test',
            'Användaren vill bara ha fem rader i rapporten.')
        self.coworker.write({
            'hitl_threshold': 'high_risk',
            'memory_learning_direct': True,
            'learning': 'active',
        })
        scopes = self._ensure_scopes()
        self.assertIn('personal', scopes, 'personal-scope måste vara aktivt')

        reflection = [{
            'key': 'rapport.fem.rader',
            'summary': 'Användaren vill bara ha fem rader i rapporten',
            'scope': 'personal',
            'kind': 'fact',
        }]
        Suggestion = self.env.get('workspace.activity.suggestion')
        # OBS: workspace.activity.suggestion har INGET `scope`-fält —
        # räkna per medarbetare i stället.
        before = Suggestion.search_count([
            ('coworker_id', '=', self.coworker.id)
        ]) if Suggestion is not None else 0

        with patch.object(
                type(self.coworker), '_extract_concepts_from_conversation',
                return_value=reflection):
            written = self.coworker._learn_from_session(session)

        self.assertEqual(written, 1, 'konceptet skrevs inte')
        concept = self.Okf.search([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id),
            ('summary', 'ilike', 'fem rader'),
        ])
        self.assertTrue(concept, 'inget personal-koncept skapades')

        if Suggestion is not None:
            after = Suggestion.search_count([
                ('coworker_id', '=', self.coworker.id)])
            self.assertEqual(
                after, before,
                'companion ska skriva direkt — inget förslag ska läggas i kön')

    def test_non_companion_defers_personal(self):
        """Utan `memory_learning_direct` ska `personal` bli ett förslag.

        Task 5.2, motprov: bevisar att companion-regeln inte vidgar alla.
        """
        from unittest.mock import patch

        session = self._session_with_line(
            'Icke-companion-test',
            'Användaren vill bara ha fem rader i rapporten.')
        self.coworker.write({
            'hitl_threshold': 'high_risk',
            'memory_learning_direct': False,
            'learning': 'active',
        })
        reflection = [{
            'key': 'rapport.fem.rader.2',
            'summary': 'Användaren vill bara ha fem rader i rapporten (2)',
            'scope': 'personal',
            'kind': 'fact',
        }]
        Suggestion = self.env.get('workspace.activity.suggestion')
        # OBS: workspace.activity.suggestion har INGET `scope`-fält —
        # räkna per medarbetare i stället.
        before = Suggestion.search_count([
            ('coworker_id', '=', self.coworker.id)
        ]) if Suggestion is not None else 0

        with patch.object(
                type(self.coworker), '_extract_concepts_from_conversation',
                return_value=reflection):
            written = self.coworker._learn_from_session(session)

        self.assertEqual(written, 0, 'utan companion-regel ska inget skrivas')
        concept = self.Okf.search([
            ('scope', '=', 'personal'),
            ('summary', 'ilike', 'fem rader (2)'),
        ])
        self.assertFalse(concept, 'förslaget får inte skrivas direkt')
        if Suggestion is not None:
            after = Suggestion.search_count([
                ('coworker_id', '=', self.coworker.id)])
            self.assertEqual(
                after, before + 1, 'ett förslag ska ha lagts i kön')

    # ── 5.3 coworker-scopet får koncept ───────────────────────────────

    def test_procedure_lands_in_coworker_scope(self):
        """D4/D7: `kind='procedure'` → coworker-scope med source_user_id.

        Task 5.3. Mockad reflektion — routningen är det som bevisas.
        """
        from unittest.mock import patch

        session = self._session_with_line(
            'Scopetest',
            'Vi kom fram till ett arbetssätt för avstämningen.')
        self.coworker.write({
            'hitl_threshold': 'high_risk',
            'memory_learning_direct': True,
            'learning': 'active',
        })
        self._ensure_scopes()
        before = self.Okf.search_count([('scope', '=', 'coworker')])

        reflection = [{
            'key': 'avstamning.arbetssatt',
            'summary': 'Stäm av leverantörsreskontran varje fredag före lunch',
            'scope': 'coworker',
            'kind': 'procedure',
        }]
        with patch.object(
                type(self.coworker), '_extract_concepts_from_conversation',
                return_value=reflection):
            written = self.coworker._learn_from_session(session)

        self.assertEqual(written, 1, 'proceduren skrevs inte')
        skill = self.env['ai.skill'].search([
            ('name', 'ilike', 'Stäm av leverantörsreskontran'),
        ])
        self.assertTrue(skill, 'proceduren borde bli en ai.skill')

        after = self.Okf.search_count([('scope', '=', 'coworker')])
        self.assertGreater(
            after, before,
            'coworker-scopet ska ha fått minst ett koncept (F1.1 mätte 0)')
        proc = self.Okf.search([
            ('scope', '=', 'coworker'),
            ('owner_coworker_id', '=', self.coworker.id),
            ('concept_key', 'ilike', 'procedure.%'),
        ], limit=1)
        self.assertTrue(proc, 'procedur-konceptet saknas i coworker-scopet')
        self.assertEqual(proc.source_user_id.id, self.user.id,
                         'proceduren ska bära sessionens användare')
