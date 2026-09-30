# -*- coding: utf-8 -*-
"""Scenariotester för odoo-model-tools (6.2, 6.3, 6.4, 8.5).

End-to-end-flöden som enhetstesterna i `test_odoo_model_tools.py` inte
täcker: en affärsuppgift från prompt till verifierat tillstånd.

Körs med: `odoo -d <db> -u ai_agent_core --test-enable --test-tags=odoo_model_tools`
"""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged('odoo_model_tools', 'post_install', '-at_install')
class TestSaleOrderScenario(TransactionCase):
    """6.2: 'skapa en offert åt ACME' → create → HITL → action_confirm."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'ACME AB'})
        cls.product = cls.env['product.product'].create({
            'name': 'Konsulttimme',
            'list_price': 1200.0,
        })

    def test_create_then_confirm_reaches_sale(self):
        """Hela kedjan: skapa utkast, bekräfta, verifiera state=sale."""
        from odoo.addons.ai_agent_core.core.tools import (
            _tool_odoo_create, _tool_odoo_call_method, _tool_odoo_search)

        # 1. Skapa ordern via verktyget (affärslagret) — odoo_create
        out = json.loads(_tool_odoo_create(
            self.env, 'sale.order',
            {'partner_id': self.partner.id}))
        self.assertTrue(out.get('ok'), out)
        order = self.env['sale.order'].browse(out['id'])

        # 2. Lägg en rad på den skapade ordern
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': 2,
        })

        self.assertEqual(order.state, 'draft', 'ny order är utkast')
        self.assertEqual(order.partner_id, self.partner)

        # 3. Bekräfta via affärsmetoden — odoo_call_method (HITL-gated)
        res = json.loads(_tool_odoo_call_method(
            self.env, 'sale.order', id=order.id, method='action_confirm'))
        self.assertTrue(res.get('ok'), res)

        order.invalidate_recordset()
        self.assertEqual(order.state, 'sale',
                         'action_confirm ska föra ordern till sale')

        # 4. Verifiera via sökverktyget att tillståndet syns — begär
        #    `state` explicit, det ingår inte i default-fälten.
        found = json.loads(_tool_odoo_search(
            self.env, 'sale.order', [('id', '=', order.id)],
            fields=['id', 'name', 'state']))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['state'], 'sale')

    def test_call_method_rejects_non_business_method(self):
        """odoo_call_method är begränsat till action_*/button_* (+vitlista).

        Generell Python-exekvering är en non-goal — ett metodnamn utanför
        mönstret ska nekas, inte köras.
        """
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_call_method
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        res = json.loads(_tool_odoo_call_method(
            self.env, 'sale.order', id=order.id, method='unlink'))
        self.assertIn('error', res)


@tagged('odoo_model_tools', 'post_install', '-at_install')
class TestPowerboxScoping(TransactionCase):
    """6.3: från en sale.order-post når powerboxen bara model_ids-modeller."""

    def test_scoped_env_only_reaches_listed_models(self):
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_search

        # Powerbox-scope: bara sale.order
        env = self.env['sale.order'].with_context(
            _ai_scoped_models={'sale.order'}).env

        # sale.order är tillåten
        allowed = json.loads(_tool_odoo_search(env, 'sale.order'))
        self.assertNotIn('error', allowed if isinstance(allowed, dict) else {})

        # res.partner är UTANFÖR scopen — ska nekas
        denied = json.loads(_tool_odoo_search(env, 'res.partner'))
        self.assertIn('error', denied,
                      'en modell utanför model_ids ska nekas')

    def test_scoped_create_is_denied_too(self):
        """Scoping gäller ALLA verktyg, inte bara sökning."""
        from odoo.addons.ai_agent_core.core.tools import _tool_odoo_create
        env = self.env['sale.order'].with_context(
            _ai_scoped_models={'sale.order'}).env
        out = json.loads(_tool_odoo_create(
            env, 'res.partner', {'name': 'Ska nekas'}))
        self.assertIn('error', out)


@tagged('odoo_model_tools', 'post_install', '-at_install')
class TestSkillActivation(TransactionCase):
    """6.4: `/sale bekräfta order 123` → recipe injiceras → HITL-policy följs."""

    def test_odoo_core_skill_exists_with_sale_section(self):
        """odoo-core-skillen bär en sektion per app (sale ingår)."""
        skill = self.env['ai.skill'].search([('name', '=', 'odoo-core')], limit=1)
        self.assertTrue(skill, 'odoo-core-skillen ska finnas')
        recipe = skill.recipe_text or ''
        self.assertIn('sale', recipe.lower(),
                      'skillen ska ha en sale-sektion')

    def test_skill_carries_hitl_policy(self):
        """Recipe:n ska ange HITL-policyn — annars kan agenten inte följa den."""
        skill = self.env['ai.skill'].search([('name', '=', 'odoo-core')], limit=1)
        self.assertTrue(skill)
        recipe = (skill.recipe_text or '').lower()
        self.assertTrue(
            'hitl' in recipe or 'godkänn' in recipe or 'approval' in recipe,
            'skillen ska beskriva HITL-policyn')

    def test_odoo_specialist_has_the_skill(self):
        """Odoo-specialistens agent ska vara kopplad till odoo-core-skillen."""
        agent = self.env.ref(
            'ai_agent_core.agent_odoo_business', raise_if_not_found=False)
        if not agent:
            self.skipTest('agent_odoo_business är inte seedad i denna DB')
        names = agent.skill_ids.mapped('name')
        self.assertIn('odoo-core', names)


@tagged('odoo_model_tools', 'post_install', '-at_install')
class TestSupervisorRouting(TransactionCase):
    """8.5: Odoo-fråga delegeras till Odoo-specialisten, research till Research."""

    def test_default_coworker_is_supervisor_with_three_agents(self):
        """Default-medarbetaren ska vara supervisor med kärna + två specialister."""
        cw = self.env['ai.coworker'].search(
            [('name', '=', 'Allmän assistent')], limit=1)
        if not cw:
            self.skipTest('default-medarbetaren är inte seedad i denna DB')
        self.assertEqual(cw.orchestration_mode, 'supervisor')
        agent_names = cw.agent_ids.mapped('agent_id.name')
        self.assertTrue(len(agent_names) >= 2,
                        'supervisor kräver minst två specialister: %s'
                        % agent_names)

    def test_odoo_specialist_carries_model_tools(self):
        """Odoo-specialisten ska ha modellverktygen — det är dess uppgift."""
        agent = self.env.ref(
            'ai_agent_core.agent_odoo_business', raise_if_not_found=False)
        if not agent:
            self.skipTest('agent_odoo_business är inte seedad i denna DB')
        tool_names = agent.tool_ids.mapped('name')
        self.assertTrue(
            {'odoo_search', 'odoo_create'} & set(tool_names),
            'Odoo-specialisten ska bära modellverktygen: %s' % tool_names)

    def test_research_agent_carries_web_tools(self):
        """Research-agenten ska ha web-verktyg, inte modellverktyg."""
        agent = self.env.ref(
            'ai_agent_core.agent_research', raise_if_not_found=False)
        if not agent:
            self.skipTest('agent_research är inte seedad i denna DB')
        tool_names = set(agent.tool_ids.mapped('name'))
        self.assertTrue(
            tool_names & {'odoo_web_search', 'odoo_fetch_url'},
            'Research ska bära web-verktygen: %s' % tool_names)
