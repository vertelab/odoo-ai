# -*- coding: utf-8 -*-
import json
import logging
import pickle
import base64

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)


class OpenAIThread(models.TransientModel):
    _name = 'openai.thread'
    _description = 'OpenAI Thread'

    channel_id = fields.Many2one(comodel_name='mail.channel', string="Channel", help="")  #
    assistant = fields.Char(required=False)
    thread = fields.Char(required=False)
    run = fields.Char(required=False)
    recipient_id = fields.Many2one(comodel_name='res.users', string='Recipient')
    author_id = fields.Many2one(comodel_name='res.partner', string='Author')
    client = fields.Text()

    def thread_values(self, channel, recipient, author):
        return {
            'channel_id': channel.id,
            'recipient_id': recipient.id,
            'author_id': author.id,
            'thread': channel.name,
        }

    @api.model
    def thread_init(self, client, channel, recipient, author):
        """
            Initialize a thread, create if not available
        """
        thread_ids = self.env['openai.thread'].search([('channel_id', '=', channel.id)])
        # _logger.warning(f"Thread {thread_ids=} {channel=}")
        if len(thread_ids) == 0:
            thread = self.env['openai.thread'].create(self.thread_values(channel, recipient, author))
        else:
            thread = thread_ids[0]
        return thread

    def log_values(self, message, author, role='user', status_code=200):
        return {
            'author_id': author.id,
            'channel_id': self.channel_id.id,
            'assistant': self.assistant,
            'thread': self.thread,
            'run': self.run,
            'message': message,
            'status_code': status_code,
            'role': role
        }

    def log(self, message, author, role='user', status_code=200):
        self.env['openai.log'].create(self.log_values(message, author, role, status_code))

    def add_message(self, client, message, role='user'):
        """
            Add a Message to a Thread
        """
        self.log(message, self.author_id, role=role)

    def wait4response(self, client):
        """
            Wait for the LLM to response
        """
        msg = [{'role': 'assistant', 'content': _('Please install driver for a LLM')}]
        self.log(msg[0]['content'], self.recipient_id.partner_id, role=msg[0]['role'])
        return msg

    def _thread_unlink(self, client, channel):
        return

    def thread_unlink(self, client, channel):
        """
             Tidy up and delete the thread
        """
        for thread in self.env['openai.thread'].search([('channel_id', '=', channel.id)]):
            thread._thread_unlink(client, channel)
        self.env['openai.thread'].search([('channel_id', '=', channel.id)]).unlink()
        msg = [{'role': 'assistant', 'content': _('Reset done')}]
        self.log(msg[0]['content'], self.recipient_id.partner_id, role=msg[0]['role'])
        return msg

    def load_client(self):
        return pickle.loads(self.client)

    def client_init(self, client):
        # self.client = base64.encode(pickle.dumps(client)
        return client

    def custom_serialize(self, obj):
        from ssl import SSLContext
        data = {}
        for key, value in obj.__dict__.items():
            if isinstance(value, SSLContext):
                # Skip or handle SSLContext
                continue
            else:
                data[key] = value
        return data
