from odoo import models, fields, _

class OpenaiLog(models.Model):
    _name = 'openai.log'
    _description = 'OpenAI Log'
    _order = 'id desc'

    date = fields.Datetime(
        string='Date',
        help="""Date""",
        required=True,
        default=fields.Datetime.now,
    )
