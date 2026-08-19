# -*- coding: utf-8 -*-
"""Server action wizard for ai.coworker — prompt input + AI response."""

import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AICoworkerServerActionWizard(models.TransientModel):
    _name = 'ai.coworker.server.action.wizard'
    _description = 'AI Coworker Server Action Wizard'

    coworker_id = fields.Many2one('ai.coworker', string='Coworker', required=True, readonly=True)
    res_model = fields.Char('Model', readonly=True)
    res_id = fields.Integer('Record ID', readonly=True)
    res_model_name = fields.Char('Record Name', readonly=True)

    prompt = fields.Text(
        'Prompt',
        required=True,
        help='What should this agent do with the current record?'
    )
    result = fields.Text('Result', readonly=True)
    state = fields.Selection([
        ('input', 'Input'),
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='input', string='State')

    def action_run(self):
        """Execute the server action with the user's prompt."""
        self.ensure_one()
        self.state = 'processing'

        try:
            coworker = self.coworker_id
            record = self.env[self.res_model].browse(int(self.res_id)) if self.res_model and self.res_id else None

            # Run the coworker with the user's prompt and record context
            result = coworker.server_action(
                records=[record] if record else []
            )

            # Extract response text
            if hasattr(result, 'text'):
                response_text = result.text
            elif isinstance(result, dict) and result.get('response'):
                response_text = str(result['response'])
            else:
                response_text = str(result or '')

            self.result = response_text[:10000]  # Limit length
            self.state = 'done'

            # Post result as chatter message on the record
            if record and response_text:
                try:
                    record.message_post(
                        body=f'<p><strong>AI response:</strong><br/>{response_text[:4000]}</p>',
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
                except Exception:
                    _logger.warning('Failed to post AI result to chatter', exc_info=True)

        except Exception as e:
            _logger.error('Server action wizard error: %s', e, exc_info=True)
            self.result = str(e)[:5000]
            self.state = 'error'

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.server.action.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': self.env.context,
        }

    def action_close(self):
        """Close the wizard."""
        return {'type': 'ir.actions.act_window_close'}
