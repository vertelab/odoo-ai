# -*- coding: utf-8 -*-
"""Regressionstester för orkestreringslooparnas konstruktors-kontrakt.

Bakgrund (ai-agent-core-fault-remediation A1/B3): `_build_loop` skickade
`outcome_callback=` till `LinearLoop`, men `LinearLoop.__init__` hade inte
parametern. Resultatet var `TypeError` vid VARJE anrop i linear-läge — läget
var permanent trasigt sedan det infördes.

Det befintliga testet (`test_orchestration_types.TestLinearLoopModule`) prövar
bara att modulen kan importeras. Import-täckning är inte körvägs-täckning:
konstruktorn anropades aldrig, så felet kunde leva.

Dessa tester instansierar looparna med **samma argumentuppsättning som
driftkoden** använder, så att ett borttaget eller omdöpt argument fångas.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai_agent_core.core.loop import AgentConfig


class _FakeProvider:
    """Minsta provider-stubbe — looparna ska inte röra nätverket här."""

    is_bifrost = False

    async def aclose(self):
        return None


class _FakeAgent:
    """Minsta ai.agent-stubbe: bara de fält LinearLoop läser."""

    def __init__(self, name, model_name='test-model'):
        self.name = name
        self.ai_role = 'Test'
        self.ai_goal = 'Test goal'
        self.model_id = None
        self._model_name = model_name


class _FakeAgentRel:
    """ai.coworker.agent-rad: loopen läser .agent_id."""

    def __init__(self, agent, sequence=10):
        self.agent_id = agent
        self.sequence = sequence


@tagged('orchestration', 'ai_core', 'post_install', '-at_install')
class TestLoopConstructorContract(TransactionCase):
    """Varje loop ska acceptera de callbacks som `_build_loop` skickar."""

    def _drift_kwargs(self):
        """Argumentuppsättningen som ai_coworker._build_loop skickar.

        Hålls som en egen metod så att testet uttryckligen visar VILKA
        argument driftkoden kräver — inte bara att konstruktorn går att
        anropa på något sätt.
        """
        return {
            'agents': [_FakeAgentRel(_FakeAgent('A'), 10),
                       _FakeAgentRel(_FakeAgent('B'), 20)],
            'provider': _FakeProvider(),
            'tools': None,
            'base_model': 'test-model',
            'base_system': 'You are a test.',
            'max_rounds': 4,
            'session_headers': {'x-bf-session': 'test'},
            'outcome_callback': lambda response: None,
            'checkpoint_callback': lambda label: None,
        }

    def test_linear_loop_accepts_drift_arguments(self):
        """LinearLoop ska instansieras med samma argument som _build_loop ger.

        Fallerar på dagens kod med
        `TypeError: __init__() got an unexpected keyword argument
        'outcome_callback'`.
        """
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        kwargs = self._drift_kwargs()
        loop = LinearLoop(**kwargs)

        self.assertIsNotNone(loop)
        # Callbacks ska ha tagits emot, inte tystnats bort.
        self.assertTrue(
            callable(getattr(loop, 'outcome_callback', None)),
            'LinearLoop måste bevara outcome_callback')
        self.assertTrue(
            callable(getattr(loop, 'checkpoint_callback', None)),
            'LinearLoop måste bevara checkpoint_callback')

    def test_linear_loop_rejects_nothing_silently(self):
        """Ett okänt nyckelargument ska ge TypeError — inte sväljas.

        Skyddar mot att någon "löser" felet med `**kwargs` och därmed
        återinför den tysta degraderingen.
        """
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        kwargs = self._drift_kwargs()
        kwargs['ett_argument_som_inte_finns'] = True
        with self.assertRaises(TypeError):
            LinearLoop(**kwargs)

    def test_agent_config_carries_both_callbacks(self):
        """AgentConfig är kontraktet looparna emellan — båda fälten finns."""
        config = AgentConfig(
            model='test-model',
            outcome_callback=lambda r: None,
            checkpoint_callback=lambda label: None,
        )
        self.assertTrue(callable(config.outcome_callback))
        self.assertTrue(callable(config.checkpoint_callback))

    def test_linear_loop_calls_checkpoint_per_step(self):
        """Checkpoint ska anropas en gång per steg i pipelinen.

        Det är transaktionshygienen: utan anropet håller steget
        ACCESS SHARE-lås öppna över provider-väntan (B3).
        """
        import asyncio
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        labels = []
        kwargs = self._drift_kwargs()
        kwargs['checkpoint_callback'] = labels.append
        loop = LinearLoop(**kwargs)

        # Stegen har ingen riktig provider — vi räknar bara checkpoint-anropen
        # och låter stegets provider-fel bubbla upp. Det är OK: checkpoint sker
        # FÖRE provider-anropet, vilket är exakt det vi mäter.
        try:
            asyncio.run(loop.run('test'))
        except Exception:
            pass

        self.assertGreaterEqual(
            len(labels), 1,
            'LinearLoop måste anropa checkpoint_callback före ett steg')
        self.assertIn('round-start', labels)

    def test_linear_loop_calls_outcome_callback(self):
        """Pipelinesvaret ska gå genom outcome_callbacken."""
        import asyncio
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        seen = []
        kwargs = self._drift_kwargs()
        kwargs['outcome_callback'] = seen.append
        # Inga agenter → pipelinen returnerar direkt utan provider-anrop.
        kwargs['agents'] = []
        loop = LinearLoop(**kwargs)

        response = asyncio.run(loop.run('test'))

        self.assertEqual(len(seen), 1, 'outcome_callback ska anropas en gång')
        self.assertIs(seen[0], response)


@tagged('orchestration', 'ai_core', 'post_install', '-at_install')
class TestBuildLoopLinearMode(TransactionCase):
    """`_build_loop` i linear-läge ska returnera en fungerande LinearLoop.

    A1: innan rättningen kastade detta `TypeError` vid VARJE anrop —
    linear-läget var permanent trasigt. Testet går genom den riktiga
    coworker-posten, inte genom att konstruera loopen för hand, så att
    kopplingen `_build_loop` → `LinearLoop` bevakas.
    """

    def setUp(self):
        super().setUp()
        self.Provider = self.env['ai.provider']
        self.Model = self.env['ai.model']
        self.Agent = self.env['ai.agent']
        self.Coworker = self.env['ai.coworker']
        self.CoworkerAgent = self.env['ai.coworker.agent']

        self.provider = self.Provider.create({
            'name': 'Linear Test Provider',
            'provider_type': 'custom',
            'status': 'confirmed',
        })
        self.model = self.Model.create({
            'name': 'linear-test-model',
            'provider': self.provider.id,
        })
        self.agent_a = self.Agent.create({
            'name': 'Linear A', 'ai_role': 'Research',
            'ai_goal': 'Collect', 'model_id': self.model.id,
        })
        self.agent_b = self.Agent.create({
            'name': 'Linear B', 'ai_role': 'Analyse',
            'ai_goal': 'Analyse', 'model_id': self.model.id,
        })
        self.coworker = self.Coworker.create({
            'name': 'Linear Build Test',
            'orchestration_mode': 'linear',
        })
        self.CoworkerAgent.create({
            'coworker_id': self.coworker.id,
            'agent_id': self.agent_a.id, 'sequence': 10,
        })
        self.CoworkerAgent.create({
            'coworker_id': self.coworker.id,
            'agent_id': self.agent_b.id, 'sequence': 20,
        })
        self.coworker.invalidate_recordset()

    def test_build_loop_returns_linear_loop(self):
        """Linear-läget ska ge en LinearLoop utan TypeError."""
        from odoo.addons.ai_agent_core.core.linear import LinearLoop

        loop = self.coworker._build_loop(
            provider=object(), tools=None,
            model='linear-test-model', system_prompt='test',
        )

        self.assertIsInstance(loop, LinearLoop)

    def test_build_loop_linear_carries_checkpoint_callback(self):
        """Checkpoint-callbacken ska nå linear-loopen (B3).

        Utan den håller pipelinen ACCESS SHARE-lås öppna över
        provider-väntan — samma buggklass som T/50690.
        """
        session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Linear checkpoint test',
            'status': 'active',
        })

        loop = self.coworker._build_loop(
            provider=object(), tools=None,
            model='linear-test-model', system_prompt='test',
            session=session,
        )

        self.assertTrue(
            callable(getattr(loop, 'checkpoint_callback', None)),
            'linear-loopen måste bära checkpoint_callback')
        self.assertTrue(
            callable(getattr(loop, 'outcome_callback', None)),
            'linear-loopen måste bära outcome_callback')
