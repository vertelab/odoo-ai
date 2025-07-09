from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)


class AIQuestSessionLine(models.Model):
    _name = 'ai.quest.session.line'
    _description = 'AI Quest Session Line'
    _order = 'datetime desc'

    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    ai_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_memory_id = fields.Many2one(comodel_name="ai.memory")
    ai_quest_id = fields.Many2one(comodel_name="ai.quest")
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    ai_tool_id = fields.Many2one(comodel_name="ai.tool")
    api_type_id = fields.Many2one(comodel_name="product.attribute.value")
    commercial_partner_id = fields.Many2one(comodel_name='res.partner', string="Partner")
    data_type_id = fields.Many2one(comodel_name="product.attribute.value")
    datetime = fields.Datetime(string='Datetime', default=fields.Datetime.now())
    db_name = fields.Char(string='Database Name')
    db_uuid = fields.Char(string='Database UUID')
    display_name = fields.Char(compute="compute_display_name")
    finish_reason = fields.Char()
    llm_additional_rate = fields.Float(string='Additional rate', related="product_tmpl_id.llm_additional_rate")
    model_id = fields.Many2one(comodel_name="product.attribute.value")
    model_real = fields.Char()
    product_tmpl_id = fields.Many2one(comodel_name='product.template', string="", help="")
    run_id = fields.Char()
    system_fingerprint = fields.Char()
    token = fields.Integer()
    token_currency = fields.Many2one(comodel_name='res.currency', string="Currency", help="")
    token_monetary = fields.Monetary(currency_field="token_currency")
    token_sys = fields.Integer()
    token_type_id = fields.Many2one(comodel_name="product.attribute.value")
    user_id = fields.Many2one(comodel_name='res.users', string="User", help="")
    
    @api.model
    def new_line(self, session, aimessage, agent=None, debug=False, memory=False, tool=False):
        if aimessage and aimessage.usage_metadata:
            token_types = {
                'input_tokens': aimessage.usage_metadata.get('input_tokens', 0),
                'input_tokens_audio': aimessage.usage_metadata.get('input_token_details', {'audio': 0})['audio'],
                'input_tokens_cache_read': aimessage.usage_metadata.get('input_token_details', {'cache_read': 0})[
                    'cache_read'],
                'output_tokens': aimessage.usage_metadata.get('output_tokens', 0),
                'output_tokens_audio': aimessage.usage_metadata.get('output_token_details', {'audio': 0})['audio'],
                'output_tokens_reasoning': aimessage.usage_metadata.get('output_token_details', {'reasoning': 0})[
                    'reasoning'],
            }  # Don't count tokens twice
            token_types['input_tokens'] -= (token_types['input_tokens_audio'] + token_types['input_tokens_cache_read'])
            token_types['output_tokens'] -= (
                    token_types['output_tokens_audio'] + token_types['output_tokens_reasoning'])
        else:
            token_types = {}
        api_type_id = self.env["product.attribute.value"].search(
            [("name", "=", "sync"), ("attribute_id", "=", self.env.ref("ai_agent.product_attribute_api_type").id)],
            limit=1)
        data_type_id = self.env["product.attribute.value"].search(
            [("name", "=", "text"), ("attribute_id", "=", self.env.ref("ai_agent.product_attribute_data_type").id)],
            limit=1)
        token_type_ids = self.env["product.attribute.value"].search(
            [("attribute_id", "=", self.env.ref("ai_agent.product_attribute_token_type").id)])

        _logger.error(f"{token_types=}")
        for token_type, token in token_types.items():
            if token > 0:
                record = {
                    'ai_tool_id': tool.id if tool else False,
                    'ai_memory_id': memory.id if memory else False,
                    "ai_agent_id": agent.id if agent else (
                        session.ai_agent_id.id if session and session.ai_agent_id else None),
                    "ai_llm_id": agent.ai_agent_llm_id.id if agent and agent.ai_agent_llm_id else (
                        session.ai_agent_llm_id.id if session.ai_agent_llm_id else None),
                    "ai_quest_id": session.ai_quest_id.id if session.ai_quest_id else None,
                    "ai_quest_session_id": session.id,
                    "api_type_id": api_type_id.id,
                    "commercial_partner_id": session.commercial_partner_id.id,
                    "data_type_id": data_type_id.id,
                    "db_name": session.db_name,
                    "db_uuid": session.db_uuid,
                    "finish_reason": aimessage.response_metadata.get("finish_reason", ''),
                    "model_id": agent.ai_agent_llm_id.model_id.product_attribute_value_id.id if agent else session.ai_agent_llm_id.model_id.product_attribute_value_id.id,
                    "model_real": aimessage.response_metadata.get(
                        "model_name", None) or aimessage.response_metadata.get("model", None),
                    "product_tmpl_id": session.ai_agent_llm_id.product_tmpl_id.id,
                    "run_id": aimessage.id,
                    "system_fingerprint": aimessage.response_metadata.get("system_fingerprint", ''),
                    "token": token,
                    "token_sys": token * 12,
                    "token_type_id": self.env.ref(f"ai_agent.product_attribute_value_{token_type}").id,
                    "user_id": session.user_id.id,
                }
                line = self.create(record)
                if debug:
                    session.log("llm", f"[session] line {line.name=} {record=}")

    @api.depends("model_id", "ai_quest_session_id.session")
    def compute_display_name(self):
        for record in self:
            record.display_name = f"[{record.ai_quest_session_id.session}] {record.datetime}"

    @api.depends("tokens")
    def compute_token_sys(self):
        for record in self:
            record.token_sys = record.token * 12


class AIQuestSessionMessage(models.Model):
    _name = 'ai.quest.session.message'
    _description = 'AI Quest Session Message'
    _order = 'sequence asc'

    sequence = fields.Integer()
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    message_type = fields.Char(string='Message Type', size=64 )
    message_content = fields.Text()
    message_raw = fields.Text()
    
