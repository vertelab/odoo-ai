# -*- coding: utf-8 -*-
"""Tester för att todo- och skill-verktygen äger sin egen state (D6).

VARFÖR DESSA TESTER FINNS: `todo_write` skrev till en modulnivå-global som
sattes när en loop skapades. Två loopar i samma worker — supervisor +
specialist, eller två samtidiga sessioner — skrev därför över varandras
state. Det är ett korrekthetsfel, inte bara en läcka: den första loopens
`todo_write` hamnade i den andras lista.

Verktygen stänger nu över sin egen hållare/källa. Testet skapar två loopar
och bevisar att de inte delar lista.
"""

import asyncio

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.core.tools import (
    TodoList,
    planning_tools,
    skill_tools,
    specialist_tools,
)


@tagged('ai_core', 'post_install', '-at_install')
class TestPlanningToolsOwnTheirState(TransactionCase):
    """4.1 + 4.2: ingen modulnivå-global delas mellan loopar."""

    def _todo_write_for(self, holder):
        tools = {t.name: t for t in planning_tools(holder)}
        return tools['todo_write'].handler

    def test_two_holders_do_not_share_state(self):
        """4.1: den första hållarens todo_write skriver till sin egen lista."""
        first = TodoList()
        second = TodoList()
        write_first = self._todo_write_for(first)
        write_second = self._todo_write_for(second)

        asyncio.run(write_first(todos=[
            {'content': 'första loopens steg', 'status': 'pending'}]))
        asyncio.run(write_second(todos=[
            {'content': 'andra loopens steg', 'status': 'pending'}]))

        self.assertEqual([i['content'] for i in first.items],
                         ['första loopens steg'])
        self.assertEqual([i['content'] for i in second.items],
                         ['andra loopens steg'],
                         'den andra loopens skrivning får inte hamna i den '
                         'första listan')

    def test_no_module_level_holder_exists(self):
        """4.1: globalen ska vara BORTA, inte bara oanvänd."""
        from odoo.addons.ai_agent_core.core import tools as tools_mod
        self.assertFalse(
            hasattr(tools_mod, '_TODO_HOLDER'),
            '_TODO_HOLDER ska vara borttagen ur modulen')

    def test_no_module_level_skill_source_exists(self):
        """4.2: samma för skill-källan."""
        from odoo.addons.ai_agent_core.core import tools as tools_mod
        self.assertFalse(
            hasattr(tools_mod, '_SKILL_SOURCE'),
            '_SKILL_SOURCE ska vara borttagen ur modulen')

    def test_two_skill_sources_are_separate(self):
        """4.2: load_skill läser rätt källa för två samtidiga loopar."""
        async def source_a(name):
            return {'name': name, 'instructions': 'från A'}

        async def source_b(name):
            return {'name': name, 'instructions': 'från B'}

        load_a = {t.name: t for t in skill_tools(source_a)}['load_skill'].handler
        load_b = {t.name: t for t in skill_tools(source_b)}['load_skill'].handler

        import json
        result_a = json.loads(asyncio.run(load_a(name='saltstack')))
        result_b = json.loads(asyncio.run(load_b(name='saltstack')))

        self.assertEqual(result_a['instructions'], 'från A')
        self.assertEqual(result_b['instructions'], 'från B')
        self.assertNotEqual(result_a, result_b,
                            'två källor får inte ge samma svar')

    def test_skill_tools_without_source_reports_it(self):
        """En loop utan källa ska säga det — inte låna en annan loops källa."""
        load = {t.name: t for t in skill_tools(None)}['load_skill'].handler
        result = asyncio.run(load(name='saltstack'))
        self.assertIn('no skill source configured', result)


@tagged('ai_core', 'post_install', '-at_install')
class TestSpecialistsHaveSeparateTodos(TransactionCase):
    """4.3: supervisor-läget — varje specialist har sin egen todo-lista."""

    def test_specialists_do_not_share_todo_state(self):
        """Två specialister som delegeras i tur och ordning ska behålla
        varsin lista.

        Supervisors arbetssätt är numera en vanlig AgentLoop med
        specialisternas verktyg registrerade (SupervisorLoop pensionerades).
        Det som testas är att delegationen inte blandar ihop deras state —
        vilket den gjorde när `todo_write` skrev till en global.
        """
        first_todo = TodoList()
        second_todo = TodoList()

        class _FakeLoop:
            """Minsta möjliga loop: `run()` returnerar sin egen todo-lista."""

            def __init__(self, holder):
                self.todo_list = holder

            async def run(self, prompt):
                from odoo.addons.ai_agent_core.core.tools import (
                    planning_tools as _pt)
                write = {t.name: t for t in _pt(self.todo_list)}[
                    'todo_write'].handler
                await write(todos=[{'content': prompt, 'status': 'pending'}])

                class _Resp:
                    text = prompt
                return _Resp()

        tools = {
            t.name: t for t in specialist_tools([
                ('specialist_ett', 'första', _FakeLoop(first_todo)),
                ('specialist_tva', 'andra', _FakeLoop(second_todo)),
            ])
        }

        asyncio.run(tools['call_specialist_specialist_ett'].handler(
            query='steg ett'))
        asyncio.run(tools['call_specialist_specialist_tva'].handler(
            query='steg två'))

        self.assertEqual([i['content'] for i in first_todo.items],
                         ['steg ett'])
        self.assertEqual([i['content'] for i in second_todo.items],
                         ['steg två'],
                         'specialisterna får inte dela todo-lista')
