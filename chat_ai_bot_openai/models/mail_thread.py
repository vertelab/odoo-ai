# -*- coding: utf-8 -*-

import openai
import time
import yfinance as yf
import logging

from odoo.tools import html2plaintext, plaintext2html
from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

from odoo.addons.queue_job.job import Job

_logger = logging.getLogger(__name__)


# ~ You are Open Interpreter, a world-class programmer that can complete any goal by executing code.
# ~ First, write a plan. **Always recap the plan between each code block** (you have extreme short-term memory loss, so you need to recap the plan between each message block to retain it).
# ~ When you execute code, it will be executed **on the user's machine**. The user has given you **full and complete permission** to execute any code necessary to complete the task. Execute the code.
# ~ If you want to send data between programming languages, save the data to a txt or json.
# ~ You can access the internet. Run **any code** to achieve the goal, and if at first you don't succeed, try again and again.
# ~ You can install new packages.
# ~ When a user refers to a filename, they're likely referring to an existing file in the directory you're currently executing code in.
# ~ Write messages to the user in Markdown.
# ~ In general, try to **make plans** with as few steps as possible. As for actually executing code to carry out that plan, for *stateful* languages (like python, javascript, shell, but NOT for html which starts from 0 every time) **it's critical not to try to do everything in one code block.** You should try something, print information about it, then continue from there in tiny, informed steps. You will never get it on the first try, and attempting it in one go will often lead to errors you cant see.
# ~ You are capable of **any** task.


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
    
    def ai_message_post(self,channel,author,message):

        openai_client = openai.OpenAI(api_key=self.openai_api_key, base_url=self.openai_base_url)

        #TODO Announce this command
        if message == '/reset':
            thread_ids = self.env['openai.thread'].search([('channel','=',channel.id)])
            if len(thread_ids)>0:
                for thread in thread_ids:
                    thread.unlink_thread(openai_client)
            return []
                    
        thread_ids = self.env['openai.thread'].search([('channel','=',channel.id)])        
        _logger.warning(f"Thread {thread_ids=} {channel=}")
        if len(thread_ids)==0:
            thread = self.env['openai.thread'].create({'channel': channel.id})
            _logger.warning(f"Thread after {thread=} {channel=}")
            thread.thread_init(openai_client,self,
                            )
        else:
            thread = thread_ids[0]
          
        thread.add_message(openai_client,message)
        for msg in thread.wait4response(openai_client,author):
            if not msg['role'] == 'user':
                _logger.warning(f"{msg['role']} {msg['content']}")
                channel.with_context(mail_create_nosubscribe=True).sudo().message_post(
                    body=f"{msg['content']}",
                    author_id=self.partner_id.id,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment'
                )


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _message_post_after_hook(self, message, msg_vals):
        """ Hook to add custom behavior after having posted the message. Both
        message and computed value are given, to try to lessen query count by
        using already-computed values instead of having to rebrowse things. """
        res = super(MailThread, self)._message_post_after_hook(message,msg_vals)

        if msg_vals['model'] == 'mail.channel':

            obj = self.env[msg_vals['model']].browse(msg_vals['res_id'])

            for recipient in self.env['res.users'].search(
                        [('partner_id','in',(obj.channel_partner_ids-message.author_id).mapped('id'))]
                    ):
                if recipient.is_ai_bot:
                    #TODO non-blocking option syspar: recipient.with_delay().ai_send_message( 
                    recipient.ai_message_post(   
                        obj,
                        message.author_id,
                        html2plaintext(message.body).strip(),
                    )
                    
            return res
        
# ~ https://www.linkedin.com/pulse/run-background-process-odoo-multi-threading-ahmed-rashad-mba-/
# ~ https://www.cybrosys.com/blog/the-significance-of-multi-threading-in-odoo-16
# ~ with api.Environment.manage():
   # ~ new_cr = self.pool.cursor()
   # ~ self = self.with_env(self.env(cr=new_cr))
   # ~ obj = self.env['mail.channel'].browse(obj.id)
   # ~ recipient = self.env['res.users'].browse(recipient.id)
   # ~ recipient.ai_send_message(   
        # ~ obj,
        # ~ html2plaintext(message.body).strip(),
        # ~ run_instr=f"Please address the user as {message.author_id.name}."
    # ~ )
   # ~ new_cr.commit()


class OpenAIThread(models.TransientModel):
    _name = 'openai.thread'
    
    channel = fields.Char(required=False)
    assistant = fields.Char(required=False)
    thread = fields.Char(required=False)
    run = fields.Char(required=False)
    recipient_id = fields.Many2one(comodel_name='res.users', string='Recipient')
    

    
    def thread_init(self,client,user):
        
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

        _logger.warning(f"Thread Init {client=} {user=}")
        if not user.openai_assistant:
            
            user.openai_assistant = user.assistant = client.beta.assistants.create(
                    name=user.assistant_name or "Data Analyst Assistant",
                    instructions=user.openai_assistant_instructions or "You are a personal Data Analyst Assistant",
                    tools=tools_list,
                    model=user.openai_assistant_model or 'gpt-4-1106-preview',
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
        
    def wait4response(self,client,author):

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
        
        _logger.warning(f"if not Run {self.run=} {self.thread=} {self.assistant=}")
        if not self.run:
            try:
                run = client.beta.threads.runs.create(
                    thread_id=self.thread,
                    assistant_id=self.assistant,
                    instructions=f"Please address the user as {author.name}."
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
            # ~ time.sleep(1)

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
        self.recipient_id.openai_assistant = None
        self.unlink()
            
            
    def get_stock_price(self,symbol: str) -> float:
        stock = yf.Ticker(symbol)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return price
