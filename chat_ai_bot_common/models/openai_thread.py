# -*- coding: utf-8 -*-

import logging

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)

class OpenAIThread(models.TransientModel):
    _name = 'openai.thread'
    
    channel_id = fields.Many2one(comodel_name='mail.channel',string="Channel",help="") # 
    assistant = fields.Char(required=False)
    thread = fields.Char(required=False)
    run = fields.Char(required=False)
    recipient_id = fields.Many2one(comodel_name='res.users', string='Recipient')
    author_id = fields.Many2one(comodel_name='res.users', string='Author')
    
    def thread_values(self,channel,recipient,author):
        return {
                'channel_id': channel.id,
                'recipient_id': recipient.id,
                'author': author.id,
                'thread': channel.name,
            }
    
    def thread_init(self,client,channel,recipient,author):
        """
            Initialize a thread, create if not available
        """
        thread_ids = self.env['openai.thread'].search([('channel_id','=',channel.id)])        
        #_logger.warning(f"Thread {thread_ids=} {channel=}")
        if len(thread_ids)==0:
            thread = self.env['openai.thread'].create(self.thread_values(channel,recipient,author))
        else:
            thread = thread_ids[0]
        return thread

    def log_values(self,message,role='user',status_code=200):
        return {'recipient_id':self.recipient_id.id,
                                       'channel_id': self.channel, 
                                       'assistant': self.assistant, 
                                       'thread': self.thread,
                                       'run':self.run,
                                       'message': message,
                                       'status_code': status_code,
                                       'role': role})
        
    def log(self,message,role='user',status_code=200):
        self.env['openai.log'].create(self.log_values(message,role,status_code))
        
    def add_message(self,client,message,role='user'):
        """
            Add a Message to a Thread
        """
        self.log(message)
                
    def wait4response(self,client,author):
        """
            Wait for the LLM to response
        """
        return [{'role': 'assistant','content': _('Please install driver for a LLM') }]
       
    def _unlink_thread(self,client,channel):
        return

    def unlink_thread(self,client,channel):
        """
             Tidy up and delete the thread
        """
        for thread in self.env['openai.thread'].search([('channel','=',channel.id)]):
            thread._unlink_thread(channel)
        self.env['openai.thread'].search([('channel','=',channel.id)]).unlink()
        return [{'role': 'assistant','content': _('Reset done') }]
