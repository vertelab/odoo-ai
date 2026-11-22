# -*- coding: utf-8 -*-
"""ai_agent_core_academic — modulen laddar och post_init_hook bygger pipelinen.

VARFÖR DET HÄR TESTET FINNS
===========================
Modulen har ingen modellkod: allt den tillför skapas av `post_init_hook`
(`__init__.py`) — en skill med hela receptet, åtta agenter och en
supervisor-quest som kopplar dem i sekvens. Ett fel i hooken syns därför
inte i källkoden, bara vid installation. Det här testet bevisar att
hooken har kört och att det den skapade hänger ihop.

KÖRNING
=======
    odoo-bin -c <conf> -d <db> -u ai_agent_core_academic \\
        --test-tags=/ai_agent_core_academic --stop-after-init
"""

from odoo.tests.common import TransactionCase, tagged

SKILL_NAME = 'Academic Paper Writing'
QUEST_NAME = 'Academic Paper Writer'

#: De åtta agenterna i pipelinen (namn som hooken skapar dem).
AGENT_NAMES = [
    'Intake Agent',
    'Literature Strategist',
    'Structure Architect',
    'Argument Builder',
    'Draft Writer',
    'Citation Compliance',
    'Peer Reviewer',
    'Formatter',
]


@tagged('post_install', '-at_install')
class TestAcademicPipeline(TransactionCase):
    """post_init_hook: skill, agenter och quest — och kopplingen dem emellan."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.skill = cls.env['ai.skill'].search(
            [('name', '=', SKILL_NAME)], limit=1)
        cls.quest = cls.env['ai.coworker'].search(
            [('name', '=', QUEST_NAME)], limit=1)

    def test_01_module_loaded(self):
        """Modulen är installerad — annars finns inte ai.coworker alls."""
        module = self.env['ir.module.module'].search(
            [('name', '=', 'ai_agent_core_academic')], limit=1)
        self.assertTrue(module, 'modulen finns inte i ir.module.module')
        self.assertEqual(module.state, 'installed')

    def test_02_hook_created_skill(self):
        """Hooken skapade skillen med hela receptet — inte en tom rad."""
        self.assertTrue(self.skill, 'post_init_hook skapade ingen skill')
        self.assertEqual(self.skill.category, 'general')
        self.assertIn('Pipeline Phases', self.skill.recipe_text or '',
                      'receptet är tomt — hooken skrev ingen pipeline')
        self.assertIn('Citation Compliance', self.skill.recipe_text or '')

    def test_03_hook_created_quest(self):
        """Hooken skapade supervisor-questen och satte dess flaggor."""
        self.assertTrue(self.quest, 'post_init_hook skapade ingen quest')
        self.assertTrue(self.quest.is_supervisor,
                        'questen är ingen supervisor — pipelinen routar fel')
        self.assertEqual(self.quest.status, 'active')

    def test_04_agents_are_linked_in_sequence(self):
        """Åtta agenter, kopplade till questen i stigande sekvens."""
        links = self.env['ai.coworker.agent'].search(
            [('coworker_id', '=', self.quest.id)])
        self.assertEqual(len(links), len(AGENT_NAMES),
                         'questen har %d agenter, förväntade %d'
                         % (len(links), len(AGENT_NAMES)))
        sequences = links.mapped('sequence')
        self.assertEqual(sequences, sorted(sequences),
                         'agenterna är inte ordnade i sekvens')
        names = links.mapped('agent_id.name')
        for name in AGENT_NAMES:
            self.assertIn(name, names, 'agenten %r saknas i questen' % name)

    def test_05_agents_carry_the_skill(self):
        """Varje agent bär skillen — annars har den inget recept att följa."""
        links = self.env['ai.coworker.agent'].search(
            [('coworker_id', '=', self.quest.id)])
        for link in links:
            self.assertIn(self.skill, link.agent_id.skill_ids,
                          'agenten %r saknar skillen' % link.agent_id.name)

    def test_06_hook_is_idempotent(self):
        """Att köra hooken igen skapar inga dubbletter."""
        before_skills = self.env['ai.skill'].search_count(
            [('name', '=', SKILL_NAME)])
        before_quests = self.env['ai.coworker'].search_count(
            [('name', '=', QUEST_NAME)])
        from .. import post_init_hook
        post_init_hook(self.env)
        self.assertEqual(
            self.env['ai.skill'].search_count([('name', '=', SKILL_NAME)]),
            before_skills, 'hooken skapade en dubblett av skillen')
        self.assertEqual(
            self.env['ai.coworker'].search_count([('name', '=', QUEST_NAME)]),
            before_quests, 'hooken skapade en dubblett av questen')
