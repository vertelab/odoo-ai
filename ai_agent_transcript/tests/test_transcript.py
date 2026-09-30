# -*- coding: utf-8 -*-
"""Tester för ai_agent_transcript (powerbox/transcript → ai_agent_core).

Körs med: odoo --test-enable -u ai_agent_transcript (eller checkmodule -t).
"""

from odoo.tests import common, tagged
from unittest.mock import patch


@tagged('post_install', '-at_install')
class TestAIComposer(common.TransactionCase):
    """6.1: composer hittas via find_composer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Test Powerbox',
            'status': 'active',
        })
        cls.task_model = cls.env['ir.model'].search(
            [('model', '=', 'project.task')], limit=1)

    def _composer(self, focused_models=None):
        vals = {
            'name': 'Test Composer',
            'interface_key': 'html_field_record',
            'coworker_id': self.coworker.id,
            'default_prompt': 'Skriv innehåll',
        }
        if focused_models:
            vals['focused_models'] = [(6, 0, [self.task_model.id])]
        return self.env['ai.composer'].create(vals)

    def test_find_specific_model(self):
        """6.1a: composer med focused_models matchar rätt modell."""
        comp = self._composer(focused_models=[self.task_model.id])
        found = self.env['ai.composer'].find_composer(
            'html_field_record', 'project.task')
        self.assertEqual(found, comp)

    def test_find_generic_fallback(self):
        """6.1b: composer med tom focused_models matchar alla modeller.

        OBS: system-default `ai_composer_html_field` finns redan (tom
        focused_models). Vi skapar en EGEN composer på en unik interface_key
        så testet inte kolliderar med default-datan.
        """
        comp = self.env['ai.composer'].create({
            'name': 'Test Generic',
            'interface_key': 'powerbox_chat',
            'coworker_id': self.coworker.id,
            'default_prompt': 'Generisk',
        })
        found = self.env['ai.composer'].find_composer(
            'powerbox_chat', 'res.partner')
        self.assertEqual(found, comp)

    def test_find_no_match(self):
        """6.1c: ingen composer för en oanvänd interface_key → tom."""
        found = self.env['ai.composer'].find_composer(
            'powerbox_channel', 'res.partner')
        self.assertFalse(found)

    def test_find_specific_beats_generic(self):
        """6.1e: specifik modell-matchning vinner över generisk default."""
        comp = self._composer(focused_models=[self.task_model.id])
        found = self.env['ai.composer'].find_composer(
            'html_field_record', 'project.task')
        self.assertEqual(found, comp)
        # För en annan modell faller vi tillbaka på generisk default
        generic = self.env['ai.composer'].find_composer(
            'html_field_record', 'res.partner')
        self.assertNotEqual(generic, comp)
        self.assertTrue(generic)

    def test_system_default_protected(self):
        """6.1d: system-default composer kan inte tas bort."""
        comp = self._composer()
        comp.write({'is_system_default': True})
        with self.assertRaises(Exception):
            comp.unlink()


@tagged('post_install', '-at_install')
class TestTranscriptContext(common.TransactionCase):
    """6.2: transcript_context byggs."""

    def test_transcript_context_with_selection(self):
        """6.2a: text_selection inkluderas."""
        cw = self.env['ai.coworker'].create({
            'name': 'Test Transcript',
            'status': 'active',
        })
        sess = self.env['ai.coworker.session'].create({
            'coworker_id': cw.id,
            'status': 'active',
            'interface_key': 'html_field_text_select',
            'text_selection': 'Detta är vald text',
        })
        self.assertIn('Detta är vald text', sess.transcript_context)

    def test_transcript_context_empty(self):
        """6.2b: utan kontext → tom (eller minimal)."""
        cw = self.env['ai.coworker'].create({
            'name': 'Test Transcript 2',
            'status': 'active',
        })
        sess = self.env['ai.coworker.session'].create({
            'coworker_id': cw.id,
            'status': 'active',
        })
        self.assertIsInstance(sess.transcript_context, str)


class _FakeResponse:
    text = 'mockat svar'
    input_tokens = 1
    output_tokens = 1
    model = 'mock'


class _FakeProvider:
    """Fake provider — undviker LLM-anrop och aclose-krasch."""

    async def aclose(self):
        pass


class _FakeLoop:
    """Fake AgentLoop — undviker LLM-anrop i powerbox-testet."""

    def __init__(self, *a, **kw):
        pass

    async def run(self, prompt):
        return _FakeResponse()


@tagged('post_install', '-at_install')
class TestPowerboxTranscript(common.TransactionCase):
    """6.3: powerbox-körning med interface_key sätter session-fält.

    6.4: regression — core powerbox() utan transcript-modul påverkas inte.
    """

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Test Powerbox Transcript',
            'status': 'active',
        })
        self.env['ai.coworker.init_type'].create({
            'coworker_id': self.coworker.id,
            'init_type': 'powerbox',
            'enabled': True,
        })
        # Patcha provider + loop så powerbox() inte kräver en riktig LLM.
        self._patches = [
            patch('odoo.addons.ai_agent_core.core.loop.AgentLoop', _FakeLoop),
            patch('odoo.addons.ai_agent_core.core.provider.ProviderFactory.from_coworker',
                  staticmethod(lambda cw: (_FakeProvider(), None))),
            patch('odoo.addons.ai_agent_core.core.provider.get_default_provider',
                  lambda *a, **kw: (_FakeProvider(), None)),
            patch('odoo.addons.ai_agent_core.core.provider.get_default_model_name',
                  lambda *a, **kw: 'mock'),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        super().tearDown()

    def test_powerbox_sets_interface_key_on_session(self):
        """6.3a: interface_key + text_selection hamnar på sessionen."""
        Session = self.env['ai.coworker.session']
        before = Session.search([('coworker_id', '=', self.coworker.id)])

        self.coworker.with_context(
            _ai_interface_key='html_field_text_select',
            _ai_text_selection='Vald text för omskrivning',
        ).powerbox(prompt='Skriv om detta')

        after = Session.search([
            ('coworker_id', '=', self.coworker.id),
        ]) - before
        self.assertTrue(after, 'powerbox ska ha skapat en session')
        self.assertEqual(after.interface_key, 'html_field_text_select')
        self.assertEqual(after.text_selection, 'Vald text för omskrivning')

    def test_powerbox_without_context_leaves_fields_empty(self):
        """6.3b: utan interface_key-context lämnas fälten tomma (no-op)."""
        Session = self.env['ai.coworker.session']
        before = Session.search([('coworker_id', '=', self.coworker.id)])

        self.coworker.powerbox(prompt='Vanlig prompt')

        after = Session.search([
            ('coworker_id', '=', self.coworker.id),
        ]) - before
        self.assertTrue(after)
        self.assertFalse(after.interface_key)
        self.assertFalse(after.text_selection)

    def test_powerbox_explicit_kwargs(self):
        """6.3c: interface_key kan ges som explicit kwarg."""
        Session = self.env['ai.coworker.session']
        before = Session.search([('coworker_id', '=', self.coworker.id)])

        self.coworker.powerbox(
            prompt='Sammanfatta',
            interface_key='voice_transcription_component',
            text_selection='Transkription här',
        )

        after = Session.search([
            ('coworker_id', '=', self.coworker.id),
        ]) - before
        self.assertEqual(after.interface_key, 'voice_transcription_component')
        self.assertEqual(after.text_selection, 'Transkription här')

    def test_core_powerbox_signature_unchanged(self):
        """6.4: core powerbox() fungerar oförändrat utan transcript-kontext.

        Regression: bryggan får inte ändra core-kontraktet — anrop med bara
        prompt (inga transcript-kwargs) ska gå igenom som förut.
        """
        import inspect
        from odoo.addons.ai_agent_core.models.ai_coworker import AICoworker
        sig = inspect.signature(AICoworker.powerbox)
        params = list(sig.parameters)
        # Core-parametrarna finns kvar
        for p in ('prompt', 'res_model', 'res_id', 'record'):
            self.assertIn(p, params)

    def test_transcript_context_after_powerbox(self):
        """6.3d: transcript_context byggs efter en powerbox-körning."""
        Session = self.env['ai.coworker.session']
        before = Session.search([('coworker_id', '=', self.coworker.id)])

        self.coworker.with_context(
            _ai_interface_key='html_field_text_select',
            _ai_text_selection='Rad att skriva om',
        ).powerbox(prompt='Skriv om')

        after = Session.search([
            ('coworker_id', '=', self.coworker.id),
        ]) - before
        self.assertIn('Rad att skriva om', after.transcript_context)
