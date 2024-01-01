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
    
    def get_stock_price(self,symbol: str) -> float:
        stock = yf.Ticker(symbol)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return price



    def ai_send_message(self,openai_client,channel,run_instr,message):

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



        thread = self.env['openai.thread'].browse(channel)
        if not thread:
            thread = self.env['openai.thread'].create({'channel': channel})
            thread.thread_init(openai_client,
                            self.openai_assistant_name or "Data Analyst Assistant",
                            self.openai_assistant_instructions or "You are a personal Data Analyst Assistant",
                            self.openai_assistant_tools or tools_list,
                            self.openai_assistant_model or 'gpt-4-1106-preview',
                            self.openai_api_key or "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",
                            self.openai_base_url)
        
        


        # Step 3: Add a Message to a Thread
        try:
            message = openai_client.beta.threads.messages.create(
                thread_id=thread.thread,
                role="user",
                content=message
            )
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: The server could not be reached {e.__cause__}")
        except openai.RateLimitError as e:
            _logger.warning(f"OPENAI: Ratelimit {e.status_code} {e.response}")
        except openai.APIStatusError as e:
            _logger.warning(f"OPENAI: Status error {e.status_code} {e.response}")
        
        
        

        # Step 4: Run the Assistant
        run = openai_client.beta.threads.runs.create(
            thread_id=thread.thread,
            assistant_id=thread.assistant,
            instructions="Please address the user as Mervin Praison."
        )

        _logger.warning(f"Model_dump: {run.model_dump_json(indent=4)}")
        msgs = []
        while True:
            # Wait for 5 seconds
            time.sleep(1)

            # Retrieve the run status
            run_status = openai_client.beta.threads.runs.retrieve(
                thread_id=thread.thread,
                run_id=run.id
            )
            _logger.warning(f"Model_dump: {run.model_dump_json(indent=4)}")

            # If run is completed, get messages
            if run_status.status == 'completed':
                messages = openai_client.beta.threads.messages.list(
                    thread_id=thread.thread
                )

                # Loop through messages and print content based on role
                for msg in messages.data:
                    msgs.append({'role':msg.role,'content':msg.content[0].text.value})
                    _logger.warning(f"{msg.role.capitalize()}: {msg.content=}")
                return msgs
                break
            elif run_status.status == 'requires_action':
                _logger.warning(f"Function calling")

                required_actions = run_status.required_action.submit_tool_outputs.model_dump()
                _logger.warning(f"{required_actions}")

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
                openai_client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.thread,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )
                
            else:
                _logger.warning("Waiting for the Assistant to process...")                    
                time.sleep(1)

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
        _logger.warning(f"{recipient_ids=}")
        if not self._context.get('ai_send_message',False):
            client = openai.OpenAI(api_key="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")
            msgs = recipient_ids[0].ai_send_message(client,msg_vals['res_id'],html2plaintext(message.body).strip(),f"Please address the user as {recipient_ids[0].name}.")
            _logger.warning(f"{msgs=}")
            for msg in msgs:
                if not msg['role'] == 'user':
                    obj.with_context(ai_send_message=True,mail_create_nosubscribe=True).sudo().message_post(
                        body=f"{msg['role'].capitalize()}: {msg['content']}",
                        author_id=recipient_ids[0].id,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment'
                    )
        else:
            _logger.warning(f"{recipient_ids[0].id=}")
        
        # ~ raise UserError(f'{message.model=},{message.author_id=} {obj.channel_partner_ids=} {recipient_ids[0].openai_api_key=}')
        
        
        #message=mail.message(359,),{'email_add_signature': True, 'record_name': 'Marc Demo, Mitchell Admin', 'author_id': 3, 'author_guest_id': False, 'email_from': '"Mitchell Admin" <admin@yourcompany.example.com>', 'model': 'mail.channel', 'res_id': 17, 'body': 'sfsdfsd', 'subject': False, 'message_type': 'comment', 'parent_id': False, 'subtype_id': 1, 'partner_ids': set(), 'attachment_ids': []}
        
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

    
    def init_thread(self,client,assistant_name,assistant_instructions,assistant_tools,
                         assistant_model='gpt-4-1106-preview',api_key=None,base_url=None):

                             
        self.assistant = client.beta.assistants.create(
                name=assistant_name,
                instructions=assistant_instructions,
                tools=assistant_tools,
                model=assistant_model,
            ).id
        
        
        # Step 2: Create a Thread
        self.thread = client.beta.threads.create().id
        
    
