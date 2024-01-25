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

_logger = logging.getLogger(__name__)

load_dotenv()

openai_api_key = os.getenv('OPENAI_API_KEY')

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
    def open_interpreter_client_init(self, user):
        try:
            client = OpenInterpreter()
            return client
        except Exception as e:
            _logger.warning(f"OpenInterpreter: The server could not be reached {e.__cause__}")
            self.log(f"{e}", user.partner_id, role='system')

    @api.model
    def open_interpreter_thread_init(self, client, channel, recipient, author):
        # TODO driver type from recipient, is it us?
        thread = self.thread_init(client, channel, recipient, author)
        _logger.warning(f"Thread Init {client=} {recipient=}")
        if not recipient.openai_assistant:
            recipient.openai_assistant = uuid.UUID.hex
        thread.assistant = recipient.openai_assistant
        thread.thread = uuid.UUID.hex
        return thread

    def open_interpreter_add_message(self, client, message, role='user'):
        """
            Add a Message to a Thread
        """
        self.log(message, self.author_id, role=role)
        self.write({'message': message, 'role': role})

    def open_interpreter_wait4response(self, interpreter_client):
        # TODO log does not save the correct author (it should be AI-bot)

        msgs = interpreter_client.chat(message=self.message, display=False, stream=False, )
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
