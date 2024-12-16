from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    llm_additional_rate = fields.Float(string="LLM Additional Rate")