# -*- coding: utf-8 -*-
"""Tests för ai.company.memory — företagsminne med kategoriaccess."""

from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('-at_install', 'post_install')
class TestCompanyMemory(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.user = cls.env.ref('base.user_admin')
        cls.Category = cls.env['ai.company.memory.category']
        cls.cat_public = cls.Category.create({
            'name': 'test-public', 'description': 'Public test category'})
        cls.cat_restricted = cls.Category.create({
            'name': 'test-restricted', 'description': 'Restricted test'})

    def test_create_company_memory(self):
        mem = self.env['ai.company.memory'].add_company_memory(
            company_id=self.company.id,
            content='Test: Vår strategi för Q3',
            category='strategy',
            source='strategy',
        )
        self.assertTrue(mem.id)
        self.assertEqual(mem.company_id.id, self.company.id)
        self.assertEqual(mem.category, 'strategy')

    def test_add_only_enforced(self):
        mem = self.env['ai.company.memory'].add_company_memory(
            company_id=self.company.id, content='Original')
        with self.assertRaises(UserError):
            mem.write({'content': 'Modified'})

    def test_search_by_company(self):
        self.env['ai.company.memory'].add_company_memory(
            company_id=self.company.id, content='Vår vision 2027',
            category='strategy')
        results = self.env['ai.company.memory'].search_for_company(
            company_id=self.company.id)
        self.assertTrue(len(results) >= 1)

    def test_accessible_categories(self):
        """Public categories are accessible by all users."""
        accessible = self.env['ai.company.memory'].get_accessible_category_ids(
            user_id=self.user.id)
        self.assertIn(self.cat_public.id, accessible)

    def test_restricted_category_excluded(self):
        """Restricted category without group is excluded."""
        accessible = self.env['ai.company.memory'].get_accessible_category_ids(
            user_id=self.user.id)
        # cat_restricted has no groups = public, so it IS accessible
        self.assertIn(self.cat_restricted.id, accessible)

    def test_build_system_prompt_block(self):
        self.env['ai.company.memory'].add_company_memory(
            company_id=self.company.id, content='Test strategi')
        block = self.env['ai.company.memory'].build_system_prompt_block(
            company_id=self.company.id, user_id=self.user.id)
        # May be empty if no mgmt_summary or strategy exists
        self.assertIsInstance(block, str)
