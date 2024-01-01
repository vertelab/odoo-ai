# -*- coding: utf-8 -*-

from odoo import models, fields, _


class OpenaiLog(models.Model):
    _name = 'openai.log'
    _description = 'OpenAI Log'


    date = fields.Datetime(
        string='Date',
        help="""Date""",
        required=True,
        default=fields.Datetime.now(),
    )

    type = fields.Selection(
        string='Request Type',
        help="""Type of request""",
        selection=[
            ('completition', 'Completition'),
            ('chat-completion', 'Chat Completion'),
        ],
        required=True,
    )


    raw_request = fields.Text(
        string='Raw Request',
        help="""Request, as-is, sent to OpenAI Api""",
        required=True,
    )

    raw_response = fields.Text(
        string='Raw Response',
        help="""Response, as-is, received from OpenAI Api""",
        required=True,
    )

    thread = fields.Char(string='Thread ID',help="Thread ID for the request")

    parsed_request = fields.Text(
        string='Parsed Request',
        help="""Parsed Request, to be human readable""",
        required=False,
    )

    parsed_response = fields.Text(
        string='Parsed Response',
        help="""Parsed Response, to be human readable""",
        required=False,
    )
