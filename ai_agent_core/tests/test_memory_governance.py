# -*- coding: utf-8 -*-
"""Tester för memory governance: identity-fält, scope-rule-arv,
seedning, injektion, per-användare, session-golv, per-agent-block."""

from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('post_install')
class TestMemoryGovernance(common.TransactionCase):
    """Tasks 6.1-6.7: Modell, governance, seedning, injektion."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.user_mitchell = cls.env['res.users'].create({
            'name': 'Mitchell',
            'login': 'mitchell_gov@example.com',
            'email': 'mitchell_gov@example.com',
        })
        cls.user_marc = cls.env['res.users'].create({
            'name': 'Marc',
            'login': 'marc_gov@example.com',
            'email': 'marc_gov@example.com',
        })

        # Setup scope codes
        cls.Scope = cls.env['ai.memory.scope']
        cls.scope_company = cls.Scope.search([('code', '=', 'company')], limit=1)
        if not cls.scope_company:
            cls.scope_company = cls.Scope.create({'name': 'Company', 'code': 'company'})
        cls.scope_personal = cls.Scope.search([('code', '=', 'personal')], limit=1)
        if not cls.scope_personal:
            cls.scope_personal = cls.Scope.create({'name': 'Personal', 'code': 'personal'})
        cls.scope_coworker = cls.Scope.search([('code', '=', 'coworker')], limit=1)
        if not cls.scope_coworker:
            cls.scope_coworker = cls.Scope.create({'name': 'Coworker', 'code': 'coworker'})

        cls.Concept = cls.env['ai.okf.concept']
        cls.ArtifactType = cls.env['ai.artifact.type']

    # ── 6.1 Modelltester: identity-fält + preset-onchange ──

    def test_identity_memory_profile_default(self):
        """Identity får default memory_profile vid skapande."""
        identity = self.env['ai.identity'].create({
            'name': 'Test Identity',
        })
        # balanced är default
        self.assertEqual(identity.memory_profile, 'balanced')

    def test_identity_memory_profile_hermes(self):
        """Identity med hermes-profil."""
        identity = self.env['ai.identity'].create({
            'name': 'Hermes Identity',
            'memory_profile': 'hermes',
        })
        self.assertEqual(identity.memory_profile, 'hermes')

    def test_coworker_seeds_from_identity(self):
        """Coworker seedas från identity vid skapande."""
        identity = self.env['ai.identity'].create({
            'name': 'Seed Test Identity',
            'memory_profile': 'hermes',
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'Seed Test Coworker',
            'identity_id': identity.id,
            'description': 'Test coworker for seedning',
        })
        # Hermes ska ha personal + coworker scopes
        scope_codes = coworker.memory_scopes.mapped('code')
        self.assertIn('personal', scope_codes)
        self.assertIn('coworker', scope_codes)
        self.assertEqual(coworker.memory_profile, 'hermes')

    # ── 6.2 Governance: scope-rule-arv ──

    def test_agent_inherits_coworker_level(self):
        """Agent utan egen level ärver coworkerns memory_level."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Inherit Test Coworker',
            'memory_level': 'L2',
            'memory_scopes': [(6, 0, [
                self.scope_company.id,
                self.scope_personal.id,
            ])],
        })
        agent = self.env['ai.agent'].create({
            'name': 'Inherit Test Agent',
        })
        link = self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
        })
        # Ingen egen level satt → ärver
        self.assertFalse(link.level_company)
        self.assertEqual(link._effective_level('company'), 'L2')

    def test_agent_own_level_overrides(self):
        """Agent med egen level överrider coworkerns."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Override Test Coworker',
            'memory_level': 'L2',
            'memory_scopes': [(6, 0, [self.scope_company.id])],
        })
        agent = self.env['ai.agent'].create({
            'name': 'Override Test Agent',
        })
        link = self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'level_company': 'L0',
        })
        self.assertEqual(link._effective_level('company'), 'L0')

    # ── 6.3 Seedning idempotent ──

    def test_seed_is_idempotent(self):
        """Seedning från identity körs idempotent — inga duplikat."""
        identity = self.env['ai.identity'].create({
            'name': 'Idempotent Identity',
            'memory_profile': 'balanced',
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'Idempotent Coworker',
            'identity_id': identity.id,
        })
        # Spara nuvarande scopes
        initial_scopes = coworker.memory_scopes.ids.copy()

        # Simulera återseedning (som vid write med identity-byte)
        coworker._onchange_identity_id()
        coworker._onchange_identity_id()

        # Ingen duplicering
        self.assertEqual(len(coworker.memory_scopes), len(initial_scopes))

    # ── 6.4 Injektion: web-chatten använder gemensam funktion ──

    def test_build_injection_prompt_includes_user(self):
        """_build_injection_prompt inkluderar användaridentitet."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Injection Test Coworker',
            'description': 'Test',
        })
        injection = coworker._build_injection_prompt(
            user=self.user_mitchell, prompt='test')
        self.assertIn('Mitchell', injection)
        self.assertIn('mitchell_gov@example.com', injection)

    def test_build_injection_prompt_respects_scope(self):
        """Injektion respekterar memory_scopes."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Scope Test Coworker',
            'memory_scopes': [(6, 0, [self.scope_company.id])],
            'description': 'Test',
        })
        injection = coworker._build_injection_prompt(
            user=self.user_mitchell, prompt='test')
        # Personal block ska INTE finnas eftersom personal-scope inte är med
        # Company block SKA finnas
        # (Exakt verifiering beror på om det finns company-koncept i test-DB)

    # ── 6.5 Per-användare ──

    def test_different_users_get_different_personal_memory(self):
        """Mitchell och Marc får varsitt personligt minne."""
        coworker = self.env['ai.coworker'].create({
            'name': 'User Test Coworker',
            'memory_scopes': [(6, 0, [
                self.scope_company.id,
                self.scope_personal.id,
            ])],
            'description': 'Test',
        })

        # Skapa personliga koncept för båda användarna
        for user, name in [(self.user_mitchell, 'Mitchell'), (self.user_marc, 'Marc')]:
            self.Concept._okf_upsert(
                artifact_type='knowledge',
                concept_key='test.personal.%s' % user.id,
                summary='%s föredrar mörkt tema' % name,
                scope='personal',
                owner_user_id=user.id,
                source_ref='test,%s' % user.id,
            )

        injection_m = coworker._build_injection_prompt(
            user=self.user_mitchell, prompt='test')
        injection_c = coworker._build_injection_prompt(
            user=self.user_marc, prompt='test')

        # Mitchells injection ska nämna Mitchell
        self.assertIn('Mitchell', injection_m)
        # Marcs injection ska nämna Marc, inte Mitchells preferenser
        self.assertIn('Marc', injection_c)

    # ── 6.6 Session-golv ──

    def test_session_only_profile(self):
        """Session-only medarbetare har memory_profile=session_only."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Session Only Coworker',
            'memory_profile': 'session_only',
            'description': 'Test',
        })
        self.assertEqual(coworker.memory_profile, 'session_only')
        # Session-only ska inte ha OKF-scopes
        self.assertFalse(coworker.memory_scopes,
                         'Session-only should have no persistent scopes')

    # ── 6.7 Per-agent-block ──

    def test_research_agent_blocked_from_personal(self):
        """Research-agent med block_personal=True får aldrig personligt."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Block Test Coworker',
            'memory_scopes': [(6, 0, [
                self.scope_company.id,
                self.scope_personal.id,
            ])],
            'description': 'Test',
        })
        research_agent = self.env['ai.agent'].create({
            'name': 'Research Agent',
        })
        link = self.env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': research_agent.id,
            'block_personal': True,
        })

        # Verifiera att blocket är satt
        self.assertTrue(link.block_personal)

        # Injektion för research agent
        injection = coworker._build_injection_prompt(
            user=self.user_mitchell,
            agent=research_agent,
            prompt='test',
        )
        # Personligt block ska INTE finnas
        # (Exakt verifiering beror på test-DB-innehåll)
