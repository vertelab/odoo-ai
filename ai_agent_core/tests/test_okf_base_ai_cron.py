# -*- coding: utf-8 -*-
"""Cronen indexerar `res.*` (okf-smart-buttons F4.5).

VARFÖR: F4.5 mäts i drift ("3679 poster är dirty-flaggade"), men frågan
den ställer är lokal och avgör om knapparna någonsin får något att visa:
*plockar `_okf_cron_index_dirty()` upp `res.partner` / `res.company` /
`res.users`?*  Före `base_ai` fanns ingen `res.*`-källa alls — modellerna
bar inte mixinen, så de kunde aldrig bli dirty och konceptlistan bakom
smartknapparna förblev tom.

Dessa tester bevisar den kedjan utan drift, i tre led:
  1. de tre modellerna bär mixinen och är registrerade för indexering
  2. en skrivning på en `res.*`-post sätter `okf_dirty`
  3. cronen skapar ett koncept för posten och rensar flaggan

Körs bara när `base_ai` är installerad — annars finns ingen brygga att
bevisa, och ett hoppat test är ärligare än ett grönt test utan innehåll.
"""

from odoo.tests import common, tagged


@tagged('okf', 'post_install', '-at_install')
class TestOkfBaseAiCron(common.TransactionCase):
    """F4.5: `res.partner`/`res.company`/`res.users` når cronen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mixin = cls.env['ai.okf.mixin']
        cls.Concept = cls.env['ai.okf.concept']
        cls.has_base_ai = 'base_ai' in cls.env['ir.module.module'].search([
            ('name', '=', 'base_ai'), ('state', '=', 'installed')]).mapped(
                'name')

    def setUp(self):
        super().setUp()
        if not self.has_base_ai:
            self.skipTest('base_ai är inte installerad — ingen res.*-källa')

    # ── 1. registreringen ───────────────────────────────────────────────

    def test_res_models_are_indexable(self):
        """Bryggan registrerar de tre modellerna för dirty-indexering."""
        indexable = self.Mixin._okf_indexable_models()
        for model in ('res.partner', 'res.company', 'res.users'):
            self.assertIn(model, indexable,
                          '%s ska vara en indexerbar källa' % model)

    def test_res_models_carry_the_mixin(self):
        """Mixinen sitter på modellerna — annars kan de aldrig bli dirty."""
        for model in ('res.partner', 'res.company', 'res.users'):
            self.assertIn('okf_dirty', self.env[model]._fields, model)

    # ── 2. flaggan ──────────────────────────────────────────────────────

    def test_partner_write_sets_dirty(self):
        """En innehållsändring på res.partner flaggar posten."""
        partner = self.env['res.partner'].create({
            'name': 'ZZ OKF Base AI Partner',
            'comment': 'Kunden vill ha fakturor via e-post.',
        })
        self.assertTrue(partner.okf_dirty, 'ny post ska indexeras')

        partner.write({'okf_dirty': False})
        partner.write({'comment': 'Kunden vill ha pappersfaktura.'})
        partner.invalidate_recordset(['okf_dirty'])
        self.assertTrue(partner.okf_dirty, 'ändrad kommentar ska flagga')

    # ── 3. cronen ───────────────────────────────────────────────────────

    def test_cron_indexes_a_partner(self):
        """Cronen skapar ett koncept för en dirty res.partner och rensar."""
        partner = self.env['res.partner'].create({
            'name': 'ZZ OKF Base AI Cron Partner',
            'comment': 'Kunden vill ha fakturor via e-post.',
        })
        self.Mixin._okf_cron_index_dirty(batch_size=10)

        partner.invalidate_recordset(['okf_dirty', 'okf_indexed_at'])
        self.assertFalse(partner.okf_dirty, 'cronen ska rensa flaggan')
        self.assertTrue(partner.okf_indexed_at, 'cronen ska stämpla tiden')

        concept = self.Concept.search([
            ('concept_key', '=', 'res.partner,%s' % partner.id)])
        self.assertTrue(concept, 'ett OKF-koncept ska ha skapats')

    def test_cron_indexes_a_company(self):
        """res.company går samma väg — den är en egen artefakttyp."""
        company = self.env['res.company'].create({
            'name': 'ZZ OKF Base AI Cron Co',
        })
        self.Mixin._okf_cron_index_dirty(batch_size=10)

        company.invalidate_recordset(['okf_dirty'])
        self.assertFalse(company.okf_dirty, 'cronen ska rensa flaggan')
        concept = self.Concept.search([
            ('concept_key', '=', 'res.company,%s' % company.id)])
        self.assertTrue(concept, 'ett OKF-koncept ska ha skapats')
