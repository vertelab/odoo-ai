from odoo import models, fields, api, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ai_api_key = fields.Char()
    ai_session_lines_ids = fields.One2many(comodel_name='ai.quest.session.line',inverse_name='product_tmpl_id',string="AI Tokens",help="") # domain|context|auto_join|limit
    is_llm = fields.Boolean()
    llm_additional_rate = fields.Float(string="LLM Additional Rate")
    llm_type = fields.Char(string='LLM Type', size=64, trim=True, help="Name of langchain class, eg ChatOpenAI or ChatMistralAI")
    token_sys = fields.Integer(string='System Tokens')
    session_line_count = fields.Integer(compute="compute_session_line_count")


    @api.depends("ai_session_lines_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.ai_session_lines_ids])


    def create_llm(self):
        for p in self:
            for model in self.env["product.template.attribute.value"].search([('product_tmpl_id','=',p.id),
                                                                            ("attribute_id", "=", self.env.ref("ai_agent.product_attribute_model").id)]):
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
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("product_tmpl_id", '=', self.id)],
        }
        return action
