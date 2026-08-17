# -*- coding: utf-8 -*-
"""Tester för ai_agent_transcript (powerbox/transcript → ai_agent_core).

Körs med: odoo --test-enable -u ai_agent_transcript (eller checkmodule -t).
"""

from odoo.tests import common, tagged


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
        """6.1b: composer med tom focused_models matchar alla modeller."""
        comp = self._composer()  # tom focused_models
        found = self.env['ai.composer'].find_composer(
            'html_field_record', 'res.partner')
        self.assertEqual(found, comp)

    def test_find_no_match(self):
        """6.1c: ingen composer → tom recordset."""
        found = self.env['ai.composer'].find_composer(
            'systray_ai_button', 'res.partner')
        self.assertFalse(found)

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
