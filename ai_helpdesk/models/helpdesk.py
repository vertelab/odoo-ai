from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError

import logging

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'

    ai_type = fields.Selection(selection_add=[('helpdesk-chat', 'Chat with ticket')], ondelete={'helpdesk-chat': 'cascade'})

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('helpdesk-chat', 'Chat with ticket')], ondelete={'helpdesk-chat': 'cascade'})

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(selection_add=[('helpdesk-chat', 'Chat with ticket')], ondelete={'helpdesk-chat': 'cascade'})

class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    ai_quest_id = fields.Many2one(comodel_name='ai.quest',string="",help="")

    @api.onchange("stage_id")
    def _onchange_stage_id(self):
        if not self.stage_id.closed:
            if self.ai_quest_id:
                self.ai_quest_id.status = 'active'
                self.ai_quest_id.channel_id.write({'active': True,})
        else:
            if self.ai_quest_id.channel_id:
                self.ai_quest_id.status = 'done'
                self.ai_quest_id.channel_id.write({'active': False})
    
    @api.onchange("name","number")
    def _onchange_name(self):
        if self.ai_quest_id:
            self.ai_quest_id.write({'name': f"[{self.number}] {self.name}"})
            if self.ai_quest_id.channel_id:
                self.ai_quest_id.channel_id.write({'name': f"[{self.number}] {self.name}"})
                
    @api.onchange("partner_id")
    def _onchange_partner(self):
        if self.ai_quest_id:
            self.ai_quest_id.write({'partner_id': self.partner_id.id if self.partner_id else False})
            
 
    @api.model
    def create(self, vals):
        ticket = super(HelpdeskTicket, self).create(vals)
        if not ticket.stage_id.closed:
            if not ticket.ai_quest_id:
                ticket.ai_quest_id = self.env['ai.quest'].create({
                    'name': f"[{ticket.number}] {ticket.name}",
                    'ai_type': 'helpdesk-chat',
                    'init_type': 'channel',
                    'status': 'active',
                    'code': """result = agents[0].prompt_agent(
                        session=session,
                        debug=quest.debug,
                        message=html2plaintext(message.body),
                        channel=channel,
                        bot_user=bot_user,
                    )"""
                })
                self.env['ai.quest.agent'].create({'ai_agent_id': self.env.ref('ai_helpdesk.ai_agent_helpdesk_chat').id,'ai_quest_id': ticket.ai_quest_id.id})
                ticket.ai_quest_id.channel_id.create({
                    'name': f"[{ticket.number}] {ticket.name}",
                    'ai_quest_id': ticket.ai_quest_id.id,
                    'description': _('Chat with helpdesk tickets'),
                })
        return ticket

            
    def Xwrite(self, vals):
        result = super(HelpdeskTicket, self).write(vals)
        for ticket in self:
            if not ticket.stage_id.closed:
                if not ticket.ai_quest_id:
                    ticket.ai_quest_id = self.env['ai.quest'].create({
                        'name': f"[{ticket.number}] {ticket.name}",
                        'ai_type': 'helpdesk-chat',
                        'init_type': 'channel',
                        'status': 'active',
                        'ai_agent_ids': [(6, 0, [self.env.ref('ai_helpdesk.ai_agent_helpdesk_chat').id])],
                        'code': """result = agents[0].prompt_agent(
                            session=session,
                            debug=quest.debug,
                            message=html2plaintext(message.body),
                            UseLang=UseLang
                        )['result'].content""",
                        'description': 'Answer my questions {message}',
                    })
                    ticket.ai_quest_id.channel_id.create({
                        'name': f"[{ticket.number}] {ticket.name}",
                        'ai_quest_id': ticket.ai_quest_id.id,
                        'description': _('Chat with helpdesk tickets'),
                    })
        return result


