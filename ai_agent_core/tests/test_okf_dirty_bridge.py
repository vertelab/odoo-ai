# -*- coding: utf-8 -*-
"""Tester för OKF-bryggan på legacy-minnena (fas 6, D7).

VARFÖR: `okf_dirty` fanns bara på `ai.memory`. `ai.personal.memory` och
`ai.company.memory` — de enda modeller där svensk BM25 faktiskt någonsin
fungerade — saknade flaggan helt. En post som skrevs där blev aldrig ett
OKF-koncept. Migreringen var därför halvfärdig: skrivsidan flyttad,
läsningen kvar i en stack som töms.

Dessa tester bevisar att bryggan är HEL: en skrivning i legacy-stacken
utsöndrar ett OKF-koncept, och cronen är idempotent.
"""

from unittest.mock import patch

from odoo.tests import common, tagged


@tagged('okf', 'memory', 'post_install', '-at_install')
class TestOkfDirtyBridge(common.TransactionCase):
    """6.4/6.5: flagga sätts vid skrivning, cron indexerar och rensar.

    UPPDATERAD 2026-09-22 (okf-mixin F2.9): indexeringen går nu via
    `ai.okf.mixin._okf_cron_index_dirty()` — `ai.memory` är pensionerad
    som OKF-konsument och `_okf_cron_index_dirty_legacy` finns inte längre.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Personal = cls.env['ai.personal.memory']
        cls.Company = cls.env['ai.company.memory']
        cls.Concept = cls.env['ai.okf.concept']
        cls.Line = cls.env['ai.memory']
        cls.user = cls.env.ref('base.user_admin')

    # ── 6.1: kolumnen finns på båda modellerna ──

    def test_okf_dirty_field_exists_on_both_models(self):
        self.assertIn('okf_dirty', self.Personal._fields)
        self.assertIn('okf_dirty', self.Company._fields)
        self.assertIn('okf_indexed_at', self.Personal._fields)
        self.assertIn('okf_indexed_at', self.Company._fields)

    # ── 6.2: flaggan sätts vid create och vid innehållsändring ──

    def test_new_personal_memory_is_dirty(self):
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Kunden vill ha fakturor via e-post.',
            'category': 'fact',
        })
        self.assertTrue(mem.okf_dirty, 'ny post ska indexeras')

    def test_new_company_memory_is_dirty(self):
        mem = self.Company.create({
            'company_id': self.env.company.id,
            'content': 'Vi använder tvåveckorssprintar.',
            'category': 'knowledge',
        })
        self.assertTrue(mem.okf_dirty)

    def test_content_is_add_only(self):
        """FYND: legacy-minnena är ADD-only — innehållet kan inte ändras.

        Detta är samma mönster som `ai.okf.concept` (ADD-only/versionskedja).
        Konsekvens för bryggan: den enda vägen till nytt innehåll är en NY
        post — vilket är precis vad `create`-kroken fångar. En hook på
        innehållsändring är därför till stor del teoretisk, men den finns
        kvar för arkivering/importance/entities.
        """
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Original.',
            'category': 'fact',
        })
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            mem.write({'content': 'Ändrat.'})

    def test_archiving_resets_dirty(self):
        """Arkivering är en livscykeländring som ska slå igenom på konceptet."""
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Arkiveras.',
            'category': 'fact',
        })
        mem.write({'okf_dirty': False})
        self.assertFalse(mem.okf_dirty)

        mem.write({'archived': True})
        mem.invalidate_recordset()
        self.assertTrue(mem.okf_dirty,
                        'arkivering gör konceptet inaktuellt')

    def test_irrelevant_write_does_not_reset_dirty(self):
        """En flagga som sätts av allt är värdelös — den ska vara specifik."""
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Kunden vill ha fakturor via e-post.',
            'category': 'fact',
        })
        mem.write({'okf_dirty': False})
        before = mem.access_count
        mem.write({'access_count': before + 1})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty,
                         'access_count rör inte innehållet')

    # ── 6.3: cronen indexerar och rensar ──

    def test_cron_indexes_personal_memory(self):
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Kunden vill ha fakturor via e-post.',
            'category': 'fact',
        })
        written = self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=10)

        self.assertGreaterEqual(written, 1)
        mem.invalidate_recordset(['okf_dirty', 'okf_indexed_at'])
        self.assertFalse(mem.okf_dirty, 'cronen ska rensa flaggan')
        self.assertTrue(mem.okf_indexed_at, 'cronen ska stämpla tiden')

        concept = self.Concept.search([
            ('concept_key', '=', 'ai.personal.memory,%s' % mem.id)])
        self.assertTrue(concept, 'ett OKF-koncept ska ha skapats')
        self.assertIn('fakturor', concept[0].summary)

    def test_cron_indexes_company_memory(self):
        mem = self.Company.create({
            'company_id': self.env.company.id,
            'content': 'Vi använder tvåveckorssprintar.',
            'category': 'knowledge',
        })
        written = self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=10)
        self.assertGreaterEqual(written, 1)

        concept = self.Concept.search([
            ('concept_key', '=', 'ai.company.memory,%s' % mem.id)])
        self.assertTrue(concept)
        self.assertIn('tvåveckorssprintar', concept[0].summary)

    def test_concept_key_is_stable(self):
        """Nyckeln måste vara STABIL — annars blir varje körning en ny version."""
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Stabil nyckel.',
            'category': 'fact',
        })
        vals_a = mem._okf_concept_vals()
        vals_b = mem._okf_concept_vals()
        self.assertEqual(vals_a['concept_key'], vals_b['concept_key'])
        self.assertEqual(vals_a['concept_key'],
                         'ai.personal.memory,%s' % mem.id)

    def test_cron_is_idempotent(self):
        """6.5: andra körningen ska inte göra något."""
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': 'Idempotent post.',
            'category': 'fact',
        })
        first = self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=10)
        self.assertGreaterEqual(first, 1)

        before = self.Concept.search_count([
            ('concept_key', '=', 'ai.personal.memory,%s' % mem.id)])
        second = self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=10)
        after = self.Concept.search_count([
            ('concept_key', '=', 'ai.personal.memory,%s' % mem.id)])

        self.assertEqual(second, 0, 'inget mer är dirty')
        self.assertEqual(before, after, 'inga nya versioner ska skapas')

    def test_empty_memory_clears_flag_without_concept(self):
        """En tom post ska inte blockera kön för evigt."""
        mem = self.Personal.create({
            'user_id': self.user.id,
            'content': '',
            'category': 'fact',
        })
        self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=10)
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty, 'tombstone: flaggan rensas ändå')

    def test_okf_owner_routing(self):
        """6.3: personal → user_id, company → company_id."""
        p = self.Personal.create({
            'user_id': self.user.id, 'content': 'P.', 'category': 'fact'})
        c = self.Company.create({
            'company_id': self.env.company.id, 'content': 'C.',
            'category': 'knowledge'})
        self.assertEqual(p._okf_owner_vals()['owner_user_id'], self.user.id)
        self.assertEqual(c._okf_owner_vals()['owner_company_id'],
                         self.env.company.id)

    def test_unknown_model_is_safe(self):
        """6.3: en modell som inte finns ska inte krascha cronen."""
        self.assertEqual(
            self.env['ai.okf.mixin']._okf_cron_index_dirty(batch_size=1), 0)

    def test_full_cron_covers_both_legacy_models(self):
        """Bryggan ska täcka båda legacy-modellerna.

        FYND (okf-mixin F2.9): testet täckte tidigare TRE modeller —
        `ai.memory` räknades in. Den är pensionerad som OKF-konsument
        (modellen är RAG-kapacitet, inte kunskap), så nu är det två.
        """
        p = self.Personal.create({
            'user_id': self.user.id, 'content': 'Personligt.',
            'category': 'fact'})
        c = self.Company.create({
            'company_id': self.env.company.id, 'content': 'Företagsvisst.',
            'category': 'knowledge'})

        total = self.env['ai.okf.mixin']._okf_cron_index_dirty()

        self.assertGreaterEqual(total, 2)
        p.invalidate_recordset(['okf_dirty'])
        c.invalidate_recordset(['okf_dirty'])
        self.assertFalse(p.okf_dirty)
        self.assertFalse(c.okf_dirty)
