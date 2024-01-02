# -*- coding: utf-8 -*-

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
    channel_id = fields.Many2one(comodel_name='mail.channel', string='Channel')
    assistant = fields.Char(required=False)
    role = fields.Char(required=False)
    status_code = fields.Integer(required=False)
    thread = fields.Char(string='Thread ID',help="Thread ID for the request")
    run = fields.Char(string='Run ID',help="Run ID for the request")
    recipient_id = fields.Many2one(comodel_name='res.users', string='Recipient')
    
    message = fields.Text(
        string='Raw Request',
        help="""Request/RESPONSE, as-is, sent to OpenAI Api""",
        required=True,
    )

    
