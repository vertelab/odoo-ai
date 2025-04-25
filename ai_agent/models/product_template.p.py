from odoo import models, fields, api, _
from odoo.addons.ai_agent.models.ai_agent_llm import LICENCES

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
    azure_endpoint = fields.Char(string="Azure Endpoint")
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
            for model in attrs_value:
                self.env['ai.agent.llm'].create({
                    'ai_api_key': p.ai_api_key,
                    'model_id': model.id,
                    'product_tmpl_id': p.id,
                    'name': f"{p.name}-{model.name}",
                })

    def action_get_session_lines(self):
        action = {
            'name': 'Tokens',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            # #if VERSION >= "18.0"
            'view_mode': 'list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,form',
            # #endif
            'target': 'current',
            'domain': [("product_tmpl_id", '=', self.id)],
        }
        return action

class ProductAttributeValue(models.Model):
    _inherit = 'product.attribute.value'

    licence = fields.Selection(selection=LICENCES, string='Licence', default='commercial')
    is_embedded = fields.Boolean(string='Is Embedded')
    has_endpoint = fields.Boolean(string="Has Endpoint")

    tpm = fields.Integer(string="Token Per Minute")
    rpm = fields.Integer(string="Request Per Minute")


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    tpm = fields.Integer(string="Token Per Minute", related="product_attribute_value_id.tpm")
    rpm = fields.Integer(string="Request Per Minute", related="product_attribute_value_id.rpm")