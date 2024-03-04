# -*- coding: utf-8 -*-

from interpreter import OpenInterpreter, interpreter
import time
from dotenv import load_dotenv
import yfinance as yf
import logging
import uuid
import os

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

# Logger settings. In this module we set messages in green textcolor
_logger = logging.getLogger(__name__)
# Set textcolor into green, must be head in message. (gn for green)
cyan = "\033[36m"
# Reset Color to default, must be tail in message. (cr for color reset)
color_reset = "\033[0m"

load_dotenv()

#openai_ = os.getenv('OPENAI_API_KEY')

# https://github.com/KillianLucas/open-interpreter/blob/main/docs/NCU_MIGRATION_GUIDE.md

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


# TODO driver type on recipient so we know if it's us
# TODO use litellm.utils.function_to_dict https://litellm.vercel.app/docs/completion/function_call#litellmfunction_to_dict---convert-functions-to-dictionary-for-openai-function-calling

class OpenAIThread(models.TransientModel):
    _inherit = 'openai.thread'

    message = fields.Text(string='Message')
    role = fields.Char(string='Role')

    @api.model
    def client_init(self, user):
        _logger.warning("\033[1;36mOpen Interpreter / client_init\033[0m]") 
        if user.llm_type != "open_interpreter":
            _logger.warning("\033[1;36mOpen Interpreter / client_init --> NOT OI LLM!\033[0m]") 
            return super().client_init(user)
            #return super(OpenAIThread, self).client_init(user)
        
        try: 
            _logger.warning("\033[1;36mOpen Interpreter / client_init --> Set OI as 'Client'...\033[0m]") 
            client = OpenInterpreter()
            return client
        except Exception as e:
            _logger.warning(f"Open Interpreter: The server could not be reached {e.__cause__}")
            self.log(f"{e}", user.partner_id, role='system')
        
    @api.model
    def thread_init(self, client, channel, recipient, author):
        _logger.warning("\033[1;36mOpen Interpreter / thread_init\033[0m]") 
        if recipient.llm_type != "open_interpreter":
            _logger.warning("\033[1;36mOpen Interpreter / thread_init --> NOT OI LLM!\033[0m]") 
            return super().thread_init(client, channel, recipient, author)
        _logger.warning("\033[1;36mOpen Interpreter / thread_init --> super() 1 sent!\033[0m]") 
        # TODO driver type from recipient, is it us?
        thread = super().thread_init(client, channel, recipient, author)
        _logger.warning("\033[1;36mOpen Interpreter / thread_init --> super() 2 sent!\033[0m]") 
        _logger.warning(f"Thread Init {client=} {recipient=}")
        if not recipient.openai_assistant:
            recipient.openai_assistant = uuid.UUID.hex
        thread.assistant = recipient.openai_assistant
        thread.thread = uuid.UUID.hex
        client.llm.api_base = recipient.openai_base_url or 'https://api.openai.com/v1' #http://192.168.1.68:8000/v1"
        client.offline = True # Disables online features like Open Procedures
        client.llm.model = recipient.openai_model or 'gpt-4-1106-preview' #"openai/v1" # Tells OI to send messages in OpenAI's format
        client.llm.api_key = recipient.openai_api_key #"fake_key" # Lit
        return thread

    def add_message(self, client, message, user_id ,role='user'):
        if user_id.llm_type != "open_interpreter":
            return super(OpenAIThread, self).add_message(client, message, user_id, role)
        """
            Add a Message to a Thread
        """
        self.log(message, self.author_id, role=role)
        self.write({'message': message, 'role': role})

    def wait4response(self, client, user_id):
        if user_id.llm_type != "open_interpreter":
            return super(OpenAIThread, self).wait4response(client, user_id)
        # TODO log does not save the correct author (it should be AI-bot)

        # Using local LLMs:::


        msgs = client.chat(message=self.message, display=False, stream=False, )
        #msgs = interpreter.chat(self.message)
        self.log(f"OpenInterpreter: {self.message=} {msgs=}", self.recipient_id.parent_id, status_code=200,
                 role='OpenInterpreter', )
        _logger.warning(f"OpenInterpreter: {self.message=} {msgs=}")
        return msgs

    def _unlink_thread(self, client, channel):
        self.recipient_id.openai_assistant = None

    def get_stock_price(self, symbol: str) -> float:
        stock = yf.Ticker(symbol)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return price
