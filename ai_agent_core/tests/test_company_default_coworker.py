# -*- coding: utf-8 -*-
"""Tester för bolagets förvalda coworker (autonomy-tab-and-company-default).

Default-coworkern är ett BOLAGSBESLUT (`res.company.ai_default_coworker_id`),
inte en egenskap hos en medarbetare. `_ai_default_coworker()` är den enda
uppslagningen; anroparen äger sin egen fallback.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCompanyDefaultCoworker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Coworker = cls.env['ai.coworker']
        cls.coworker = cls.Coworker.create({
            'name': 'Test — default-kandidat',
            'status': 'active',
        })

    def setUp(self):
        super().setUp()
        # Varje test börjar utan valt default — inget läcker mellan test.
        self.company.ai_default_coworker_id = False

    def test_tomt_falt_ger_none(self):
        """Utan valt default returneras None (ingen gissning)."""
        self.assertFalse(self.company._ai_default_coworker())

    def test_aktiv_coworker_returneras(self):
        """Bolagets val returneras när coworkern är aktiv."""
        self.company.ai_default_coworker_id = self.coworker
        self.assertEqual(
            self.company._ai_default_coworker(), self.coworker)

    def test_inaktiv_coworker_ger_none(self):
        """En avaktiverad coworker ska inte förväljas."""
        self.company.ai_default_coworker_id = self.coworker
        self.coworker.active = False
        self.assertFalse(self.company._ai_default_coworker())

    def test_valet_ar_per_bolag(self):
        """Två bolag kan peka på olika coworkers."""
        other = self.env['res.company'].create({'name': 'Test — andra bolaget'})
        second = self.Coworker.create({
            'name': 'Test — andra bolagets val',
            'status': 'active',
        })
        self.company.ai_default_coworker_id = self.coworker
        other.ai_default_coworker_id = second
        self.assertEqual(
            self.company._ai_default_coworker(), self.coworker)
        self.assertEqual(other._ai_default_coworker(), second)

    def test_is_default_finns_inte(self):
        """Flaggan på coworkern är borta — default bor på bolaget."""
        self.assertNotIn('is_default', self.Coworker._fields)
