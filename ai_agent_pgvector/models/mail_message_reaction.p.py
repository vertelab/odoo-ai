import logging
import json

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError, ValidationError
from odoo.addons.ai_agent_pgvector.fields.fields import PgVector
from odoo.addons.ai_agent_pgvector.models.embedding_mixin import EmbeddingMixin

_logger = logging.getLogger(__name__)

class MailMessageReaction(models.Model):
    _name = 'mail.message.reaction'
    _inherit = ["mail.message.reaction","llm.embedding.mixin"]

    message_question_id = fields.Many2one(related="message_id.parent_id")
    message_answer_id = fields.Many2one(related="message_id")
    message_question_embedding = PgVector(dimension=768)
    message_answer_embedding = PgVector(dimension=768)

    @api.model_create_multi
    def create(self, vals_list):
        message_reaction_ids = super(MailMessageReaction,self).create(vals_list)
        for message_reaction_id in message_reaction_ids:
            # #if VERSION >= "17.0"
            chanel_id = self.env["discuss.channel"].search([("id", "in", [message_reaction_id.message_answer_id.res_id])],limit=1)
            # #elif VERSION <= "16.0"
            chanel_id = self.env["mail.channel"].search([("id", "in", [message_reaction_id.message_answer_id.res_id])],limit=1)
            # #endif
            user_id = self.env["res.users"].search([("partner_id", "=", message_reaction_id.message_answer_id.author_id.id)],limit=1)
            ai_quest_id = user_id.ai_quest_id if chanel_id.channel_type == "chat" else chanel_id.ai_quest_id
            if message_reaction_id.message_question_id and ai_quest_id and ai_quest_id.feedback_llm:
                message_reaction_id.message_question_embedding = ai_quest_id.feedback_llm.get_embedding().embed_query(message_reaction_id.message_question_id.body)
                message_reaction_id.message_answer_embedding = ai_quest_id.feedback_llm.get_embedding().embed_query(message_reaction_id.message_answer_id.body)
        return message_reaction_ids



  
        
        
        
        
        
        
        
        
        
        
        
        

        
