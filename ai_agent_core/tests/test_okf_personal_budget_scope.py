# -*- coding: utf-8 -*-
"""Budgeten mäter minne, inte allt en person äger (fix-personal-memory-budget-scope).

VARFÖR: `_personal_memory_domain()` filtrerade på `scope='personal'` och
`owner_user_id` — men `scope` är en ÄGAR-axel, inte en ART-axel.
`_okf_upsert()` härleder `scope` ur ägaren, så varje PDF, kalenderpost och
mail som en användare råkade äga blev `scope='personal'` och belastade
minnesbudgeten. Mätt i drift 2026-10-25: 93 % av det som mättes var
kunskap, inte minne — och budgeten slog till mot fel sak.

Dessa tester bevisar:
  1. domänen filtrerar på `artifact_type_id.kind = 'memory'`
  2. bruket räknar minnen, inte dokument
  3. arkiveringskandidaterna följer samma domän
  4. kunskapstaket mäter `kind='knowledge'` och varnar men spärrar aldrig
  5. `char_budget = 0` (default) beter sig exakt som före ändringen

Stall-varningen (D5) testas i `test_okf_stall_warning.py`.
"""

import logging
from unittest.mock import patch

from odoo.tests import common, tagged

_logger = logging.getLogger(__name__)


@tagged('okf', 'post_install', '-at_install')
class TestPersonalMemoryBudgetScope(common.TransactionCase):
    """D1: en definition, fyra konsumenter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.AType = cls.env['ai.artifact.type']
        cls.memory_type = cls._ensure_type('zz-budget-memory', 'memory')
        cls.knowledge_type = cls._ensure_type('zz-budget-knowledge', 'knowledge')
        cls.user = cls.env['res.users'].create({
            'name': 'ZZ Budget User', 'login': 'zz_budget_user',
        })
        cls.other = cls.env['res.users'].create({
            'name': 'ZZ Budget Other', 'login': 'zz_budget_other',
        })

    @classmethod
    def _ensure_type(cls, name, kind):
        atype = cls.AType.search([('name', '=', name)], limit=1)
        if not atype:
            atype = cls.AType.create({'name': name, 'kind': kind})
        return atype

    def _concept(self, user, atype, summary, key):
        """Skapa ett koncept direkt — vi testar domänen, inte indexeraren."""
        return self.Concept.create({
            'artifact_type_id': atype.id,
            'scope': 'personal',
            'concept_key': key,
            'version': 1,
            'title': key,
            'summary': summary,
            'owner_user_id': user.id,
        })

    # ── D1: domänen ─────────────────────────────────────────────────────

    def test_domain_filters_on_kind_memory(self):
        """Domänen bär `kind='memory'` — inte bara scope och ägare."""
        domain = self.Concept._personal_memory_domain(self.user)
        self.assertIn(('artifact_type_id.kind', '=', 'memory'), domain)
        self.assertIn(('scope', '=', 'personal'), domain)
        self.assertIn(('owner_user_id', '=', self.user.id), domain)

    def test_domain_accepts_record_and_id(self):
        """Både en res.users-post och ett id ska ge samma domän."""
        by_record = self.Concept._personal_memory_domain(self.user)
        by_id = self.Concept._personal_memory_domain(self.user.id)
        self.assertEqual(by_record, by_id)

    # ── D1: bruket ──────────────────────────────────────────────────────

    def test_usage_counts_memory_not_knowledge(self):
        """Kunskap som ägs av användaren belastar INTE minnesbudgeten."""
        self._concept(self.user, self.memory_type, 'x' * 100, 'mem-a')
        self._concept(self.user, self.knowledge_type, 'y' * 5000, 'know-a')
        self.assertEqual(self.Concept._personal_memory_usage(self.user), 100)

    def test_usage_ignores_other_owners(self):
        """En annan användares minne räknas inte in."""
        self._concept(self.user, self.memory_type, 'x' * 100, 'mem-b')
        self._concept(self.other, self.memory_type, 'y' * 900, 'mem-c')
        self.assertEqual(self.Concept._personal_memory_usage(self.user), 100)

    def test_usage_ignores_archived_and_superseded(self):
        """Arkiverade och ersatta rader injiceras inte och ska inte räknas."""
        live = self._concept(self.user, self.memory_type, 'x' * 40, 'mem-d')
        gone = self._concept(self.user, self.memory_type, 'y' * 400, 'mem-e')
        gone.write({'archived': True})
        replaced = self._concept(self.user, self.memory_type, 'z' * 400, 'mem-f')
        replaced.write({'superseded_by_id': live.id})
        self.assertEqual(self.Concept._personal_memory_usage(self.user), 40)

    # ── D1: arkiveringskandidaterna ─────────────────────────────────────

    def test_archive_candidates_use_same_domain(self):
        """Kandidaterna är minnen — aldrig dokument användaren äger."""
        self._concept(self.user, self.memory_type, 'x' * 10, 'mem-g')
        self._concept(self.user, self.knowledge_type, 'y' * 10, 'know-b')
        candidates = self.Concept._personal_memory_archive_candidates(
            self.user, limit=10)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates.artifact_type_id.kind, 'memory')

    # ── D2: kunskapstaket ───────────────────────────────────────────────

    def test_knowledge_budget_default_is_no_cap(self):
        """Default `0` = inget tak — en oconfigurerad installation är oförändrad."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_knowledge.char_budget', '0')
        self.assertEqual(self.Concept._personal_knowledge_budget(), 0)

    def test_knowledge_budget_invalid_value_falls_back_to_zero(self):
        """Ett trasigt värde får inte krascha indexeringen."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_knowledge.char_budget', 'inte-ett-tal')
        self.assertEqual(self.Concept._personal_knowledge_budget(), 0)

    def test_knowledge_usage_counts_knowledge(self):
        """Kunskapstaket mäter `kind='knowledge'`, inte minnet."""
        self._concept(self.user, self.memory_type, 'x' * 700, 'mem-h')
        self._concept(self.user, self.knowledge_type, 'y' * 60, 'know-c')
        self.assertEqual(self.Concept._personal_knowledge_usage(self.user), 60)

    def test_knowledge_budget_warns_but_never_raises(self):
        """Fältet heter `char_budget` men får ALDRIG spärra en skrivning."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_knowledge.char_budget', '10')
        self._concept(self.user, self.knowledge_type, 'y' * 500, 'know-d')
        # Beviset är att anropet går igenom: ingen UserError kastas.
        self.Concept._check_personal_knowledge_budget(self.user)

    def test_knowledge_budget_quiet_under_cap(self):
        """Under taket ska ingen varning registreras."""
        self.env['ir.config_parameter'].sudo().set_param(
            'odoomind.personal_knowledge.char_budget', '10000')
        self._concept(self.user, self.knowledge_type, 'y' * 50, 'know-e')
        with patch.object(_logger, 'warning') as warn:
            self.Concept._check_personal_knowledge_budget(self.user)
        self.assertFalse(warn.called)
