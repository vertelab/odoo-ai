from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    llm_additional_rate = fields.Float(string="LLM Additional Rate")
    is_llm = fields.Boolean()
    llm_type = fields.Char(string='LLM Type', size=64, trim=True, help="Name of langchain class, eg ChatOpenAI or ChatMistralAI")
    token_sys = fields.Integer(string='System Tokens')
    ai_session_lines_ids = fields.One2many(comodel_name='ai.quest.session.line',inverse_name='product_tmpl_id',string="AI Tokens",help="") # domain|context|auto_join|limit
