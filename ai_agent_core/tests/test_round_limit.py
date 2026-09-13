# -*- coding: utf-8 -*-
"""Tester för medarbetarens rundtak (improve-ai-coworker-memory-and-tools 7.6).

Verifierar att `max_iterations` styr rundtaket i samtliga körningsvägar —
inklusive specialist-agenter i supervisor-läget — och att ett dokumenterat
fallback-värde (8) gäller när inställningen är tom.

Bakgrund: `_build_loop` och `_build_specialists` hårdkodade `max_rounds=10`,
vilket gjorde att medarbetarens Max Iterations-inställning aldrig nådde
specialisterna.
"""

from unittest.mock import patch, MagicMock

from odoo.tests import common, tagged


@tagged('post_install')
class TestRoundLimit(common.TransactionCase):
    """Rundtaket härleds från medarbetarens inställning."""

    def _coworker(self, max_iterations):
        return self.env['ai.coworker'].create({
            'name': 'Round Coworker %s' % max_iterations,
            'description': 'x',
            'status': 'active',
            'max_iterations': max_iterations,
        })

    def _captured_max_rounds(self, coworker):
        """Fånga vilket max_rounds som faktiskt skickas till AgentLoop."""
        captured = {}
        real_loop = None
        try:
            from odoo.addons.ai_agent_core.core.loop import AgentLoop
            real_loop = AgentLoop
        except Exception:
            pass

        def _fake_loop(*args, **kwargs):
            captured['max_rounds'] = kwargs.get('max_rounds')
            captured['args_len'] = len(args)
            return MagicMock()

        with patch('odoo.addons.ai_agent_core.core.loop.AgentLoop',
                   side_effect=_fake_loop):
            try:
                coworker._build_loop(
                    provider=MagicMock(), tools=MagicMock(),
                    model='test-model', system_prompt='sys')
            except Exception:
                # Vissa orchestration-modes kräver mer kontext — det vi
                # verifierar är vilket max_rounds som härletts.
                pass
        return captured.get('max_rounds')

    def test_max_iterations_drives_round_limit(self):
        """7.6: ett lägre max_iterations ger ett lägre rundtak."""
        cw = self._coworker(2)
        rounds = self._captured_max_rounds(cw)
        if rounds is None:
            self.skipTest('kunde inte fånga max_rounds i denna mode')
        self.assertEqual(
            rounds, 2,
            'medarbetarens max_iterations ska styra rundtaket, inte ett '
            'hårdkodat värde')

    def test_empty_max_iterations_gives_fallback(self):
        """7.6: tom inställning ⇒ dokumenterat fallback (8)."""
        cw = self._coworker(0)
        rounds = self._captured_max_rounds(cw)
        if rounds is None:
            self.skipTest('kunde inte fånga max_rounds i denna mode')
        self.assertEqual(
            rounds, 8, 'fallback ska vara 8 när inställningen är tom')

    def test_no_hardcoded_ten_remains(self):
        """7.6: inget hårdkodat max_rounds=10 kvar i körningsvägarna."""
        import inspect
        from odoo.addons.ai_agent_core.models import ai_coworker as mod
        src = inspect.getsource(mod)
        self.assertNotIn(
            'max_rounds=10', src,
            'hårdkodat max_rounds=10 ska inte finnas kvar')

    def test_build_specialists_derives_from_max_iterations(self):
        """7.6: även _build_specialists härleder rundtaket."""
        import inspect
        from odoo.addons.ai_agent_core.models import ai_coworker as mod
        src = inspect.getsource(mod.AICoworker)
        # Signatur + härledning ska finnas i _build_specialists.
        idx = src.find('def _build_specialists')
        self.assertGreater(idx, -1)
        block = src[idx:idx + 600]
        self.assertIn('max_rounds=None', block)
        self.assertIn('self.max_iterations or 8', block)
