# -*- coding: utf-8 -*-
import json
import logging
import pickle
import base64

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

# Logger settings. In this module we set messages in green textcolor
_logger = logging.getLogger(__name__)
# Set textcolor into green, must be head in message. (gn for green)
blue = "\033[34m"
# Reset Color to default, must be tail in message. (cr for color reset)
color_reset = "\033[0m"


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

    #@api.model
    def thread_init(self, client, channel, recipient, author):
        _logger.warning("\033[1;36mCommon / thread_init\033[0m]") 
        """
            Initialize a thread, create if not available
        """
        thread_ids = self.env['openai.thread'].search([('channel_id', '=', channel.id)])
        if len(thread_ids) == 0:
            thread = self.env['openai.thread'].create(self.thread_values(channel, recipient, author))
        else:
            thread = thread_ids[0]
        return thread

    def log_values(self, message, author, role='user', status_code=200):
        _logger.warning("\033[1;36mCommon / log_values\033[0m]") 
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
        _logger.warning("\033[1;36mCommon / log\033[0m]") 
        self.env['openai.log'].create(self.log_values(message, author, role, status_code))

    def add_message(self, client, message, user_id, role='user'):
        _logger.warning("\033[1;36mCommon / add_message\033[0m]") 
        """
            Add a Message to a Thread
            meant to be overrriden
        """
        return False

    def wait4response(self, client, user_id):
        _logger.warning("\033[1;36mCommon / wait4response\033[0m]") 
        """ Returns the form action URL, for form-based acquirer implementations. #meant to be overrriden """
        return False

    def _thread_unlink(self, client, channel):
        _logger.warning("\033[1;36mCommon / _thread_unlink\033[0m")
        #TODO When is this supposed to be called?
        return

    def unlink(self):
        _logger.warning("\033[1;36mCommon / unlink\033[0m]")
        #for record in self:
        #    record.thread_unlink(client)
        ##TODO Where Im is supposed to get the client from?
        return super(OpenAIThread, self).unlink()
    
    def thread_unlink(self, client, channel):
        _logger.warning("\033[1;36mCommon / thread_unlink\033[0m]") 
        """ 
             Tidy up and delete the thread 
             TODO When is this supposed to be called?
        """
        for thread in self.env['openai.thread'].search([('channel_id', '=', channel.id)]):
            thread._thread_unlink(client, channel)
        self.env['openai.thread'].search([('channel_id', '=', channel.id)]).unlink()
        msg = [{'role': 'assistant', 'content': _('Reset done')}]
        self.log(msg[0]['content'], self.recipient_id.partner_id, role=msg[0]['role'])
        return msg

    def load_client(self):
        _logger.warning("\033[1;36mCommon / load_client\033[0m]") 
        return pickle.loads(self.client)

    @api.model
    def client_init(self, client):
         _logger.warning("\033[1;36mCommon / client_init\033[0m]") 
         return False
         #meant to be overrriden
