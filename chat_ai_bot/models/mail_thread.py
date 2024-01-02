# -*- coding: utf-8 -*-

import openai
import time
import yfinance as yf
import logging

from odoo.tools import html2plaintext, plaintext2html
from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    is_ai_bot = fields.Boolean(string="Is an AI-Bot")
    openai_api_key = fields.Char(required=False)
    openai_base_url = fields.Char(required=False)
    openai_model = fields.Char(required=False)
    openai_max_tokens = fields.Integer(required=False)
    openai_temperature = fields.Float(required=False)
    # ~ chat_method = fields.Selection(required=False)
    chat_system_message = fields.Text(required=False)
    openai_prompt_prefix = fields.Char(required=False)
    openai_prompt_suffix = fields.Char(required=False)
    openai_assistant_name = fields.Char(required=False)
    openai_assistant_instructions = fields.Text(required=False)
    openai_assistant_tools = fields.Json(required=False)
    openai_assistant_model = fields.Char(required=False)
    openai_assistant = fields.Char(required=False)
    




    def ai_send_message(self,openai_client,channel,message,run_instr=''):

        tools_list = [{
            "type": "function",
            "function": {

                "name": "get_stock_price",
                "description": "Retrieve the latest closing price of a stock using its ticker symbol",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "The ticker symbol of the stock"
                        }
                    },
                    "required": ["symbol"]
                }
            }
        }]

        if message == '/reset':
            thread_ids = self.env['openai.thread'].search([('channel','=',channel)])
            if len(thread_ids)>0:
                for thread in thread_ids:
                    thread.unlink_thread(openai_client)
            return []
                    


        thread_ids = self.env['openai.thread'].search([('channel','=',channel)])        
        _logger.warning(f"Thread {thread_ids=} {channel=}")
        if len(thread_ids)==0:
            thread = self.env['openai.thread'].create({'channel': channel})
            _logger.warning(f"Thread after {thread=} {channel=}")
            thread.thread_init(openai_client,self,
                            self.openai_assistant_name or "Data Analyst Assistant",
                            self.openai_assistant_instructions or "You are a personal Data Analyst Assistant",
                            self.openai_assistant_tools or tools_list,
                            self.openai_assistant_model or 'gpt-4-1106-preview',)
        else:
            thread = thread_ids[0]
        _logger.warning(f"Thread ccc {thread.thread=} {thread.assistant=}")

          
        thread.add_message(openai_client,message)
        msgs = thread.wait4response(openai_client,run_instr=run_instr)

        return msgs

class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _message_post_after_hook(self, message, msg_vals):
        """ Hook to add custom behavior after having posted the message. Both
        message and computed value are given, to try to lessen query count by
        using already-computed values instead of having to rebrowse things. """
        res = super(MailThread, self)._message_post_after_hook(message,msg_vals)

        obj = self.env[msg_vals['model']].browse(msg_vals['res_id'])
        recipient_ids = self.env['res.users'].search([('partner_id','in',(obj.channel_partner_ids-message.author_id).mapped('id'))])
        _logger.warning(f"direkt efter {recipient_ids=}")
        _logger.warning(f"{self._context=}")
        
        
        if not self._context.get('ai_send_message',False):
            for recipient in recipient_ids:
                if recipient.is_ai_bot:
                    
                    # ~ https://www.linkedin.com/pulse/run-background-process-odoo-multi-threading-ahmed-rashad-mba-/
                    # ~ https://www.cybrosys.com/blog/the-significance-of-multi-threading-in-odoo-16
                    # ~ with api.Environment.manage():
                       # ~ new_cr = self.pool.cursor()
                       # ~ self = self.with_env(self.env(cr=new_cr))
                       # ~ moves = self.env['stock.move'].search([
                           # ~ ('id', 'in', move_ids)]).with_env(self.env(cr=new_cr))
                   # ~ # Perform calculations and updates for each move
                       # ~ for move in moves:
                           # ~ # Logic for calculations and updates (missing details)
                       # ~ new_cr.commit()
                       
                    client = openai.OpenAI(api_key=recipient.openai_api_key, base_url=recipient.openai_base_url)
                    msgs = recipient.ai_send_message(   client,
                                                        msg_vals['res_id'],
                                                        html2plaintext(message.body).strip(),
                                                        run_instr=f"Please address the user as {message.author_id.name}."
                                                    )
                    _logger.warning(f"{msgs=}")
                    for msg in msgs:
                        if not msg['role'] == 'user':
                            obj.with_context(ai_send_message=True,mail_create_nosubscribe=True).sudo().message_post(
                                body=f"{msg['content']}",
                                author_id=recipient.id,
                                message_type='comment',
                                subtype_xmlid='mail.mt_comment'
                            )
        else:
            _logger.warning(f"No context {recipient_ids=}")

        return res
        
    def Xmessage_post(self, *,
                     body='', subject=None, message_type='notification',
                     email_from=None, author_id=None, parent_id=False,
                     subtype_xmlid=None, subtype_id=False, partner_ids=None,
                     attachments=None, attachment_ids=None,
                     **kwargs):
                         
        
                         
        raise UserError(f'{body},{subject=},{message_type=}')

class OpenAIThread(models.TransientModel):
    _name = 'openai.thread'
    
    channel = fields.Char(required=False)
    assistant = fields.Char(required=False)
    thread = fields.Char(required=False)
    run = fields.Char(required=False)
    recipient_id = fields.Many2one(comodel_name='res.users', string='Recipient')
    
    

    
    def thread_init(self,client,user,assistant_name,assistant_instructions,assistant_tools,
                         assistant_model='gpt-4-1106-preview'):
        _logger.warning(f"Thread Init {client=} {user=}")
        if not user.openai_assistant:
            
            user.openai_assistant = self.assistant = client.beta.assistants.create(
                    name=assistant_name,
                    instructions=assistant_instructions,
                    tools=assistant_tools,
                    model=assistant_model,
                ).id
        else:
            self.assistant = user.openai_assistant
            
        self.thread = client.beta.threads.create().id
        self.recipient_id = user.id
        
    def log(self,message,role='user',status_code=200):
        self.env['openai.log'].create({'recipient_id':self.recipient_id.id,
                                       'channel_id': self.channel, 
                                       'assistant': self.assistant, 
                                       'thread': self.thread,
                                       'run':self.run,
                                       'message': message,
                                       'status_code': status_code,
                                       'role': role})
        
        
    def add_message(self,client,message,role='user'):
        """
            Add a Message to a Thread
        """
        self.log(message)
        try:
            message = client.beta.threads.messages.create(
                thread_id=self.thread,
                role="user",
                content=message
            )
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: Thread The server could not be reached {e.__cause__}")
            self.log(f"OPENAI: Thread The server could not be reached {e.__cause__}",status_code=e.status_code,role='openai')
        except openai.RateLimitError as e:
            _logger.warning(f"OPENAI: Thread Ratelimit {e.status_code} {e.response}")
            self.log(f"OPENAI: Thread Ratelimit {e.status_code} {e.response}",status_code=e.status_code,role='openai')
        except openai.APIStatusError as e:
            _logger.warning(f"OPENAI: Thread Status error {e.status_code} {e.response}")
            self.log(f"OPENAI: Thread Status error {e.status_code} {e.response}",status_code=e.status_code,role='openai',)
        
    def wait4response(self,client,run_instr=''):

        if self.run:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=self.thread,
                run_id=self.run
            )
            if run_status.status == 'expired':
                self.run = None
                self.log(f"OPENAI: Status error run expired {self.run=} {run_status.status=}",status_code=400,role='openai',)
        else:
            _logger.warning(f"Run saved {self.run=}")
            self.run = None
                 
        if not self.run:
            try:
                run = client.beta.threads.runs.create(
                    thread_id=self.thread,
                    assistant_id=self.assistant,
                    instructions=run_instr or f"Please address the user as {self.recipient_id.name}."
                )
                self.log(run.model_dump_json(indent=4),role='run')
                _logger.warning(f"Model_dump: {run.model_dump_json(indent=4)} {run.id=}")
                self.run = run.id
            except openai.APIConnectionError as e:
                _logger.warning(f"OPENAI: Run The server could not be reached {e.__cause__}")
                self.log(f"OPENAI: Run The server could not be reached {e.__cause__}",status_code=e.status_code,role='openai')
                return []
            except openai.RateLimitError as e:
                _logger.warning(f"OPENAI: Run Ratelimit {e.status_code} {e.response}")
                self.log(f"OPENAI: Run Ratelimit {e.status_code} {e.response}",status_code=e.status_code,role='openai')
                return []
            except openai.APIStatusError as e:
                _logger.warning(f"OPENAI: Run Status error {e.status_code} {e.response}")
                self.log(f"OPENAI: Run Status error {e.status_code} {e.response}",status_code=e.status_code,role='openai',)
                return [{'role': 'assistant','content': f"OPENAI: Status error {e.status_code} {e.response}" }]
                
        msgs = []
        while True:
            # Wait for 5 seconds
            time.sleep(1)

            # Retrieve the run status
            run_status = client.beta.threads.runs.retrieve(
                thread_id=self.thread,
                run_id=self.run
            )
            
            self.log(f"{run_status.status=}",role='run',)
            # If run is completed, get messages
            if run_status.status == 'completed':
                messages = client.beta.threads.messages.list(
                    thread_id=self.thread
                )
                _logger.warning(f"Completed: {messages=}")

                # Loop through messages and print content based on role
                for msg in messages.data:
                    self.log(' '.join([m.text.value for m in msg.content]),msg.role)
                    if msg.run_id == self.run:
                        msgs.append({'role':msg.role,'content':msg.content[0].text.value})
                    _logger.warning(f"{msg.id=} {msg.run_id=} {msg.role=}: {msg.content=}")
                self.run = None
                break
            elif run_status.status == 'requires_action':
                
                _logger.warning(f"Function calling")

                required_actions = run_status.required_action.submit_tool_outputs.model_dump()
                _logger.warning(f"{run_status.status=} {required_actions}")
                self.log(f"{run_status.status=} {required_actions=}",role='run')
                tool_outputs = []
                import json
                for action in required_actions["tool_calls"]:
                    func_name = action['function']['name']
                    arguments = json.loads(action['function']['arguments'])
                    _logger.warning(f"{arguments=}")
                 
                    
                    if func_name == "get_stock_price":
                        output = self.get_stock_price(symbol=arguments['symbol'])
                        tool_outputs.append({
                            "tool_call_id": action['id'],
                            "output": output
                        })
                    else:
                        raise ValueError(f"Unknown function: {func_name}")

                _logger.warning("Submitting outputs back to the Assistant...")
                self.log(f"Submit tools outputs {tool_outputs=}",role='run')
                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=self.thread,
                    run_id=self.run,
                    tool_outputs=tool_outputs
                )
                
            else:
                _logger.warning(f"Waiting for the Assistant to process... {run_status.status=} {self.run=}") 
                                   
                time.sleep(1)
        return msgs


    def unlink_thread(self,client):
        try:
            client.beta.assistants.delete(self.assistant)
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: Delete The server could not be reached {e.__cause__}")
        except openai.RateLimitError as e:
            _logger.warning(f"OPENAI: Delete Ratelimit {e.status_code} {e.response}")
        except openai.APIStatusError as e:
            _logger.warning(f"OPENAI: Delete Status error {e.status_code} {e.response}")
        self.unlink()
            
            
    def get_stock_price(self,symbol: str) -> float:
        stock = yf.Ticker(symbol)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return price
