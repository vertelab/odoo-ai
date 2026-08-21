# -*- coding: utf-8 -*-
"""HITL-dispatch: när en mail-HITL godkänns → utför promotion/skick.

Ärver ai.coworker.hitl (core) och överlagrar action_approve för
mail-specifika åtgärdstyper (promote_mail, send_reply,
newsletter_move_rule). Core förblir domän-fritt.
"""

import json
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AICoworkerHITLMail(models.Model):
    _inherit = 'ai.coworker.hitl'

    def action_approve(self):
        res = super().action_approve()
        for rec in self:
            try:
                ctx = json.loads(rec.context or '{}')
            except Exception:
                ctx = {}
            mail_id = ctx.get('mail_id')
            triage = self.env['user_mail_ai.mail'].browse(
                mail_id) if mail_id else False
            try:
                if rec.action_type == 'promote_mail':
                    if triage:
                        triage._do_promotion()
                elif rec.action_type == 'send_reply':
                    if triage:
                        if ctx.get('in_thread'):
                            triage._do_reply_in_thread(rec)
                        else:
                            triage._do_send_reply(rec)
                elif rec.action_type == 'newsletter_move_rule':
                    # Standing-rule (Skiva 2): aktivera automatisk flytt
                    user = rec.user_id
                    if user:
                        user.write({'ai_newsletter_move_enabled': True})
                    if triage:
                        triage._move_to_ai_newsletters()
            except Exception as e:
                _logger.error('HITL approve dispatch failed (%s): %s',
                              rec.action_type, e)
        return res
