from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSessionMessage(models.Model):
    _name = 'ai.quest.session.message'
    _description = 'AI Quest Session Message'
    _order = 'sequence asc'

    sequence = fields.Integer()
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    message_type = fields.Char(string='Message Type', size=64 )
    message_content = fields.Text()
    message_raw = fields.Text()
    prompt = fields.Text()


    @api.model
    def add(self, session, message, **kwarg):
        start = self.env['ai.quest.session.message'].search([('ai_quest_session_id',"=",session.id)],order="sequence desc",limit=1)
        start = start.sequence + 1 if start else 0
        session_message_id = self.env['ai.quest.session.message'].create({
                "sequence": start + 1,
                "ai_quest_session_id": session.id,
                "message_type": 'add',
                "message_content": message,
                "prompt": kwarg.get('prompt',''),
            })
        return session_message_id

    
    @api.model
    def save_messages(self, session, messages,**kwarg):
        _logger.info(f"Session Save Messages {session=} {messages=}")
        start = self.env['ai.quest.session.message'].search([('ai_quest_session_id',"=",session.id)],order="sequence desc",limit=1)
        start = start.sequence + 1 if start else 0
            
        for seq, message in enumerate(messages):
            self.env['ai.quest.session.message'].create({
                "sequence": seq + start,
                "ai_quest_session_id": session.id,
                "message_type": type(message).__name__,
                "message_content": message,
                "message_raw": f"{message.items() if type(message) == 'dict' else message}",
                "prompt": kwarg.get('prompt',''),
            })