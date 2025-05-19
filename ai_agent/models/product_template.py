from odoo import models, fields, api, _
from odoo.addons.ai_agent.models.ai_agent_llm import LICENCES
from odoo.exceptions import UserError


import logging
_logger = logging.getLogger(__name__) 

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ai_api_key = fields.Char()
    fallback_api_key_name = fields.Char()
    ai_session_lines_ids = fields.One2many(comodel_name='ai.quest.session.line', inverse_name='product_tmpl_id',
                                           string="AI Tokens", help="")
    is_llm = fields.Boolean()
    llm_additional_rate = fields.Float(string="LLM Additional Rate")
    llm_library = fields.Char(string='Library', size=64, trim=True, help="Name of langchain library eg langchain_openai, langchain_groq, langchain_mistralai")
    llm_type = fields.Char(string='LLM Class', size=64, trim=True, help="Name of langchain class, eg ChatOpenAI or ChatMistralAI")
    llm_etype = fields.Char(string='Embedded Class', size=64, trim=True, help="Name of langchain class, eg OpenAIEmbeddings or MistralAIEmbeddings")
    llm_price_url = fields.Char(string='Pricelist', size=64, trim=True, help="Pricelist for tokens and llm")
    token_sys = fields.Integer(string='System Tokens')
    session_line_count = fields.Integer(compute="compute_session_line_count")
    endpoint = fields.Char(string="Endpoint")
    api_version = fields.Char(string="API version")
    
    @api.depends("ai_session_lines_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.ai_session_lines_ids])

    def create_llm(self):
        for p in self:
            attrs_value = self.env["product.template.attribute.value"].search([
                ('product_tmpl_id', '=', p.id),
                ("attribute_id", "=", self.env.ref("ai_agent.product_attribute_model").id)
            ])
            llms = self.env['ai.agent.llm'].search([('product_tmpl_id','=',p.id)])
            for model in attrs_value:
                if not f"{p.name}-{model.name}" in llms.mapped('name'):
                    # ~ raise UserError(f"{p.name}-{model.name}" in llms.mapped('name'))
                    self.env['ai.agent.llm'].create({
                        'ai_api_key': p.ai_api_key,
                        'model_id': model.id,
                        'product_tmpl_id': p.id,
                        'name': f"{p.name}-{model.name}",
                        'endpoint': model.endpoint,
                    })
        return {
            'type': 'ir.actions.act_window',
            'name': f"View LLM for {p.name}",
            'res_model': 'ai.agent.llm',
            'view_mode': 'kanban,tree,calendar,form',
            'domain': [('product_tmpl_id', '=', p.id)],
            'target': 'current',
        } 
    
    def create_llm_demo(self):
        for p in self:
            attrs_value = self.env["product.template.attribute.value"].search([
                ('product_tmpl_id', '=', p.id),
                ("attribute_id", "=", self.env.ref("ai_agent.product_attribute_model").id),
                ("name", "=", "llama-3.3-70b-versatile")
            ])
            
            for model in attrs_value:
                sanitized_name = f"{p.name}-{model.name}".lower().replace(" ", "_").replace(".", "_")
                xml_id = f"ai_agent.ai_agent_{sanitized_name}"
                
                # Check if external ID exists
                existing = self.env['ir.model.data'].search([
                    ('module', '=', 'ai_agent'),
                    ('name', '=', f"ai_agent_{sanitized_name}"),
                    ('model', '=', 'ai.agent.llm')
                ], limit=1)
                
                if existing:
                    continue  # Skip creation if exists
                    
                groq_demo_llm = self.env['ai.agent.llm'].create({
                    'ai_api_key': p.ai_api_key,
                    'model_id': model.id,
                    'product_tmpl_id': p.id,
                    'name': f"{p.name}-{model.name}",
                    'status':"confirmed",
                })
                
                self.env['ir.model.data'].create({
                    'name': f"ai_agent_{sanitized_name}",
                    'model': 'ai.agent.llm',
                    'module': 'ai_agent',
                    'res_id': groq_demo_llm.id,
                    'noupdate': True,
                })
                

    def action_get_session_lines(self):
        action = {
            'name': 'Tokens',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("product_tmpl_id", '=', self.id)],
        }
        return action

class ProductAttributeValue(models.Model):
    _inherit = 'product.attribute.value'

    licence = fields.Selection(selection=LICENCES, string='Licence', default='commercial')
    is_text2image = fields.Boolean(string='Is Text to image', default=False)
    is_vision = fields.Boolean(string='Is Vision', default=False)
    is_embedded = fields.Boolean(string='Is Embedded', default=False)
    has_endpoint = fields.Boolean(string="Has Endpoint", default=False)
    endpoint = fields.Char(string="Endpoint")

    tpm = fields.Integer(string="Token Per Minute")
    rpm = fields.Integer(string="Request Per Minute")
    context_window = fields.Integer(string="Context Window", copy=False)
    has_temperature = fields.Boolean(string="Has Temperature", default=False, copy=False)


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    tpm = fields.Integer(string="Token Per Minute", related="product_attribute_value_id.tpm")
    rpm = fields.Integer(string="Request Per Minute", related="product_attribute_value_id.rpm")
    context_window = fields.Integer(
        string="Context Window", copy=False, related="product_attribute_value_id.context_window")
    has_temperature = fields.Boolean(
        string="Has Temperature", default=False, copy=False, related="product_attribute_value_id.has_temperature")
    endpoint = fields.Char(string="Endpoint",related="product_attribute_value_id.endpoint")
