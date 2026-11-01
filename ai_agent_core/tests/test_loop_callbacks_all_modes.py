# -*- coding: utf-8 -*-
"""Checkpoint- och outcome-callbacks i ALLA körvägar (B3).

Bakgrund: `_build_loop` har åtta körvägar. Single-vägen fick båda callbacks
korrekt, men:

  * `LinearLoop` fick bara `outcome_callback` (A1, avsnitt 1),
  * `_build_specialists` byggde varje specialist-`AgentConfig` UTAN båda,
  * `ConferenceLoop` fick inga callbacks alls.

Varje specialist är en egen `AgentLoop` som kör sina egna rundor. Utan
`checkpoint_callback` håller de ACCESS SHARE-lås öppna över provider-väntan
— samma buggklass som T/50690 (mätt 35–39 s idle-in-transaction) — och utan
`outcome_callback` tappas deras mätning helt.

Detta test är strukturellt: det inspekterar de byggda `AgentConfig`-objekten
i stället för att köra en LLM. Det som ska bevakas är att callbacks finns på
plats i varje väg — inte vad de gör när de anropas.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.core.tools import ToolRegistry


def _empty_tools():
    """AgentLoop kräver ett register — None ger TypeError i __init__."""
    return ToolRegistry()


@tagged('ai_core', 'post_install', '-at_install')
class TestSpecialistCallbacks(TransactionCase):
    """10.1: varje specialist ska bära båda callbacks."""

    def setUp(self):
        super().setUp()
        self.Provider = self.env['ai.provider']
        self.Model = self.env['ai.model']
        self.Agent = self.env['ai.agent']
        self.Coworker = self.env['ai.coworker']
        self.CoworkerAgent = self.env['ai.coworker.agent']

        self.provider = self.Provider.create({
            'name': 'B3 Test Provider',
            'provider_type': 'custom',
            'status': 'confirmed',
        })
        self.model = self.Model.create({
            'name': 'b3-test-model',
            'provider': self.provider.id,
        })
        self.agent_a = self.Agent.create({
            'name': 'B3 Agent A', 'ai_role': 'Research',
            'ai_goal': 'Collect', 'model_id': self.model.id,
        })
        self.agent_b = self.Agent.create({
            'name': 'B3 Agent B', 'ai_role': 'Analyse',
            'ai_goal': 'Analyse', 'model_id': self.model.id,
        })
        self.coworker = self.Coworker.create({'name': 'B3 Coworker'})
        self.CoworkerAgent.create({
            'coworker_id': self.coworker.id,
            'agent_id': self.agent_a.id, 'sequence': 10,
        })
        self.CoworkerAgent.create({
            'coworker_id': self.coworker.id,
            'agent_id': self.agent_b.id, 'sequence': 20,
        })
        self.coworker.invalidate_recordset()

    def _specialists(self):
        return self.coworker._build_specialists(
            provider=object(), tools=_empty_tools(), model='b3-test-model',
            system_prompt='test', max_rounds=3,
            outcome_callback=lambda r: None,
            checkpoint_callback=lambda label: None,
        )

    def test_specialists_get_both_callbacks(self):
        specialists = self._specialists()
        self.assertEqual(len(specialists), 2)
        for spec in specialists:
            config = spec.loop.config
            self.assertTrue(
                callable(getattr(config, 'outcome_callback', None)),
                f'specialist {spec.name} saknar outcome_callback — '
                'dess mätning tappas')
            self.assertTrue(
                callable(getattr(config, 'checkpoint_callback', None)),
                f'specialist {spec.name} saknar checkpoint_callback — '
                'den håller ACCESS SHARE-lås över provider-väntan')

    def test_specialists_without_callbacks_still_work(self):
        """Bakåtkompatibilitet: callbacks är valfria."""
        specialists = self.coworker._build_specialists(
            provider=object(), tools=_empty_tools(), model='b3-test-model',
            system_prompt='test', max_rounds=3,
        )
        self.assertEqual(len(specialists), 2)
        for spec in specialists:
            self.assertIsNone(spec.loop.config.outcome_callback)
            self.assertIsNone(spec.loop.config.checkpoint_callback)


@tagged('ai_core', 'post_install', '-at_install')
class TestBuildLoopCallbacksAllModes(TransactionCase):
    """10.2/10.3: alla körvägar ska ge samma checkpoint-beteende."""

    def setUp(self):
        super().setUp()
        self.Provider = self.env['ai.provider']
        self.Model = self.env['ai.model']
        self.Agent = self.env['ai.agent']
        self.Coworker = self.env['ai.coworker']
        self.CoworkerAgent = self.env['ai.coworker.agent']
        self.Session = self.env['ai.coworker.session']

        self.provider = self.Provider.create({
            'name': 'B3 Modes Provider',
            'provider_type': 'custom',
            'status': 'confirmed',
        })
        self.model = self.Model.create({
            'name': 'b3-modes-model',
            'provider': self.provider.id,
        })
        self.agent_a = self.Agent.create({
            'name': 'Modes Agent A', 'ai_role': 'Research',
            'ai_goal': 'Collect', 'model_id': self.model.id,
        })
        self.agent_b = self.Agent.create({
            'name': 'Modes Agent B', 'ai_role': 'Analyse',
            'ai_goal': 'Analyse', 'model_id': self.model.id,
        })

    def _coworker(self, mode):
        cw = self.Coworker.create({
            'name': f'B3 {mode} coworker',
            'orchestration_mode': mode,
        })
        self.CoworkerAgent.create({
            'coworker_id': cw.id, 'agent_id': self.agent_a.id,
            'sequence': 10,
        })
        self.CoworkerAgent.create({
            'coworker_id': cw.id, 'agent_id': self.agent_b.id,
            'sequence': 20,
        })
        cw.invalidate_recordset()
        return cw

    def _session(self, cw):
        return self.Session.create({
            'coworker_id': cw.id,
            'name': f'B3 session {cw.orchestration_mode}',
            'status': 'active',
            'user_id': self.env.user.id,
        })

    def _specialist_configs(self, loop):
        """Plocka ut SPECIALISTERNAS AgentConfig ur en multi-agent-loop.

        ConferenceLoop och SupervisorLoop håller `agents` som en dict
        (namn → SpecialistAgent), där varje specialist bär sin egen
        AgentLoop med en AgentConfig. Det är DE configarna som ska bära
        callbacks — loopens egen `config` är SupervisorConfig, en annan typ.
        """
        agents = getattr(loop, 'agents', None) or {}
        if isinstance(agents, dict):
            agents = list(agents.values())
        configs = []
        for spec in agents:
            inner = getattr(spec, 'loop', None)
            config = getattr(inner, 'config', None)
            if config is not None and hasattr(config, 'checkpoint_callback'):
                configs.append(config)
        return configs

    def test_linear_mode_has_both_callbacks(self):
        cw = self._coworker('linear')
        loop = cw._build_loop(
            provider=object(), tools=_empty_tools(), model='b3-modes-model',
            system_prompt='test', session=self._session(cw))
        self.assertTrue(callable(getattr(loop, 'checkpoint_callback', None)))
        self.assertTrue(callable(getattr(loop, 'outcome_callback', None)))

    def test_conference_mode_specialists_have_both_callbacks(self):
        cw = self._coworker('conference')
        loop = cw._build_loop(
            provider=object(), tools=_empty_tools(), model='b3-modes-model',
            system_prompt='test', session=self._session(cw))

        configs = self._specialist_configs(loop)
        self.assertGreaterEqual(len(configs), 2,
                                'konferensvyn ska ha en config per specialist')
        for config in configs:
            self.assertTrue(
                callable(getattr(config, 'checkpoint_callback', None)),
                'B3: konferens-specialist saknar checkpoint_callback')
            self.assertTrue(
                callable(getattr(config, 'outcome_callback', None)),
                'B3: konferens-specialist saknar outcome_callback')

    def test_supervisor_mode_carries_both_callbacks(self):
        """Supervisor-läget returnerar en AgentLoop (skill-vägen).

        Specialisterna ligger som verktyg (`call_specialist_*`) i den loopen,
        inte i `loop.agents` — så det som ska bära callbacks är den yttre
        AgentLoopens config. Specialisternas EGNA loopar får sina callbacks
        via `_build_specialists` (se TestSpecialistCallbacks ovan).
        """
        cw = self._coworker('supervisor')
        loop = cw._build_loop(
            provider=object(), tools=_empty_tools(), model='b3-modes-model',
            system_prompt='test', session=self._session(cw))

        config = getattr(loop, 'config', None)
        self.assertIsNotNone(config, 'supervisor-läget ska ge en AgentLoop')
        self.assertTrue(
            callable(getattr(config, 'checkpoint_callback', None)),
            'B3: supervisor saknar checkpoint_callback')
        self.assertTrue(
            callable(getattr(config, 'outcome_callback', None)),
            'B3: supervisor saknar outcome_callback')

    def test_single_mode_has_both_callbacks(self):
        """Referensvägen — den var redan korrekt."""
        cw = self._coworker('single')
        loop = cw._build_loop(
            provider=object(), tools=_empty_tools(), model='b3-modes-model',
            system_prompt='test', session=self._session(cw))
        self.assertTrue(callable(getattr(loop.config, 'checkpoint_callback', None)))
        self.assertTrue(callable(getattr(loop.config, 'outcome_callback', None)))
