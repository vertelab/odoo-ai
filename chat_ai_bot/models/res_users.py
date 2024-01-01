# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class ResUsers(models.Model):
    _inherit = 'res.users'

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'openai_api_key',
            'openai_base_url',
            'openai_model',
            'openai_max_tokens',
            'openai_temperature',
            # ~ 'chat_method',
            'chat_system_message',
            'openai_prompt_prefix',
            'openai_prompt_suffix',
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            'openai_api_key',
            'openai_base_url',
            'openai_model',
            'openai_max_tokens',
            'openai_temperature',
            # ~ 'chat_method',
            'chat_system_message',
            'openai_prompt_prefix',
            'openai_prompt_suffix',
        ]

    openai_api_key = fields.Char(required=False)
    openai_base_url = fields.Char(required=False)
    openai_model = fields.Char(required=False)
    openai_max_tokens = fields.Integer(required=False)
    openai_temperature = fields.Float(required=False)
    # ~ chat_method = fields.Selection(required=False)
    chat_system_message = fields.Text(required=False)
    openai_prompt_prefix = fields.Char(required=False)
    openai_prompt_suffix = fields.Char(required=False)
