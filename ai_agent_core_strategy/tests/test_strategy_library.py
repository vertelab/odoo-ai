# -*- coding: utf-8 -*-
"""ai_agent_core_strategy — modulen laddar och post_init_hook bygger biblioteket.

VARFÖR DET HÄR TESTET FINNS
===========================
Modulen har ingen modellkod. Allt den tillför skapas av `post_init_hook`
(`__init__.py`): 25 skills, 6 agenter och 3 coworkers (Strategy Composer,
Strategy Advisor, Strategy Review), där agenterna kopplas till rätt skills
och till questen i sekvens. Ett fel i hooken — en skill som inte hittas, en
agent utan modell — syns bara vid installation. Det här testet bevisar att
hooken har kört och att kopplingarna håller.

KÖRNING
=======
    odoo-bin -c <conf> -d <db> -u ai_agent_core_strategy \\
        --test-tags=/ai_agent_core_strategy --stop-after-init
"""

from odoo.tests.common import TransactionCase, tagged

#: Första skillen i `_SKILLS` — hookens idempotensmarkör.
FIRST_SKILL = 'Business Model Canvas'

#: De sex agenterna (namn som hooken skapar dem).
AGENT_NAMES = [
    'Strategist',
    'Analyst',
    'Financial Analyst',
    'Strategy Writer',
    'Board Secretary',
    'Executor',
]

#: De tre coworkers som hooken skapar.
COWORKER_NAMES = ['Strategy Composer', 'Strategy Advisor', 'Strategy Review']

EXPECTED_SKILLS = 25
EXPECTED_AGENTS = 6


@tagged('post_install', '-at_install')
class TestStrategyLibrary(TransactionCase):
    """post_init_hook: skills, agenter, coworkers — och kopplingarna."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.skills = cls.env['ai.skill'].search(
            [('name', 'in', [FIRST_SKILL, 'SWOT Analysis',
                             'Porters Five Forces', 'MECE Issue Tree'])])
        cls.agents = cls.env['ai.agent'].search(
            [('name', 'in', AGENT_NAMES)])
        cls.coworkers = cls.env['ai.coworker'].search(
            [('name', 'in', COWORKER_NAMES)])

    def test_01_module_loaded(self):
        """Modulen är installerad — annars finns inte ai.coworker alls."""
        module = self.env['ir.module.module'].search(
            [('name', '=', 'ai_agent_core_strategy')], limit=1)
        self.assertTrue(module, 'modulen finns inte i ir.module.module')
        self.assertEqual(module.state, 'installed')

    def test_02_hook_created_the_skill_library(self):
        """Alla 25 skills finns, och de bär ett recept (inte bara en titel)."""
        skills = self.env['ai.skill'].search(
            [('name', 'in', [s[1] for s in self._skill_names()])])
        self.assertEqual(len(skills), EXPECTED_SKILLS,
                         'hooken skapade %d av %d skills'
                         % (len(skills), EXPECTED_SKILLS))
        for skill in skills:
            self.assertTrue(skill.recipe_text,
                            'skillen %r har tomt recept' % skill.name)

    def test_03_hook_created_the_agents(self):
        """De sex agenterna finns och är aktiva."""
        self.assertEqual(len(self.agents), EXPECTED_AGENTS,
                         'hooken skapade %d av %d agenter'
                         % (len(self.agents), EXPECTED_AGENTS))
        for agent in self.agents:
            self.assertEqual(agent.status, 'active',
                             'agenten %r är inte aktiv' % agent.name)

    def test_04_each_agent_carries_its_skills(self):
        """Varje agent har minst en skill — annars har den inget recept."""
        for agent in self.agents:
            self.assertTrue(agent.skill_ids,
                            'agenten %r saknar skills' % agent.name)

    def test_05_hook_created_the_coworkers(self):
        """De tre coworkers som hooken skapar finns och är aktiva."""
        self.assertEqual(len(self.coworkers), len(COWORKER_NAMES),
                         'hooken skapade %d av %d coworkers'
                         % (len(self.coworkers), len(COWORKER_NAMES)))
        for coworker in self.coworkers:
            self.assertEqual(coworker.status, 'active',
                             'coworkern %r är inte aktiv' % coworker.name)

    def test_06_composer_runs_the_agent_sequence(self):
        """Composer-questen har agenterna kopplade i stigande sekvens."""
        composer = self.env['ai.coworker'].search(
            [('name', '=', 'Strategy Composer')], limit=1)
        links = self.env['ai.coworker.agent'].search(
            [('coworker_id', '=', composer.id)])
        self.assertTrue(links, 'composer-questen har inga agenter')
        sequences = links.mapped('sequence')
        self.assertEqual(sequences, sorted(sequences),
                         'agenterna är inte ordnade i sekvens')

    def test_07_hook_is_idempotent(self):
        """Att köra hooken igen skapar inga dubbletter."""
        before = self.env['ai.skill'].search_count(
            [('name', '=', FIRST_SKILL)])
        from .. import post_init_hook
        post_init_hook(self.env)
        self.assertEqual(
            self.env['ai.skill'].search_count([('name', '=', FIRST_SKILL)]),
            before, 'hooken skapade en dubblett av %r' % FIRST_SKILL)

    @staticmethod
    def _skill_names():
        """Skillnamnen ur `_SKILLS` — hookens egen källa, inte en kopia."""
        from .. import _SKILLS
        return [(xmlid, name) for xmlid, name, *_rest in _SKILLS]
