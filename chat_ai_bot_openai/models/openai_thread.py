# -*- coding: utf-8 -*-

import openai
import time
import yfinance as yf
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import MissingError, AccessError, UserError

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


# TODO driver type on recipient so we know if it's us
# TODO use litellm.utils.function_to_dict https://litellm.vercel.app/docs/completion/function_call#litellmfunction_to_dict---convert-functions-to-dictionary-for-openai-function-calling

class OpenAIThread(models.TransientModel):
    _inherit = 'openai.thread'

    def thread_values(self, channel, recipient, author):
        return super(OpenAIThread, self).thread_values(channel, recipient, author)

    @api.model
    def openai_client_init(self, user):
        try:
            client = openai.OpenAI(api_key=user.openai_api_key,
                                   base_url=user.openai_base_url or 'https://api.openai.com/v1')
            return client
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: The server could not be reached {e.__cause__}")
            self.log(f"{e.response}", user.partner_id, role='system')
        except openai.APIStatusError as e:
            self.log(f"OPENAI: Status error {e.status_code} {e.response}", user.partner_id, role='system')
            _logger.warning(f"OPENAI: Status error {e.status_code} {e.response}")

    @api.model
    def openai_thread_init(self, client, channel, recipient, author):
        # TODO driver type from recipient, is it us?
        thread = self.thread_init(client, channel, recipient, author)
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

        # _logger.warning(f"Thread Init {client=} {recipient=}")
        if not recipient.openai_assistant:

            recipient.openai_assistant = thread.assistant = client.beta.assistants.create(
                name=recipient.openai_assistant_name or "Data Analyst Assistant",
                instructions=recipient.openai_assistant_instructions or "You are a personal Data Analyst Assistant",
                tools=tools_list,
                model=recipient.openai_assistant_model or 'gpt-4-1106-preview',
            ).id
        else:
            thread.assistant = recipient.openai_assistant
        thread.thread = client.beta.threads.create().id
        return thread

    def log(self, message, author, role='user', status_code=200):
        self.env['openai.log'].create({'author_id': author.id,
                                       'channel_id': self.channel_id.id,
                                       'assistant': self.assistant,
                                       'thread': self.thread,
                                       'run': self.run,
                                       'message': message,
                                       'status_code': status_code,
                                       'role': role})

    def openai_add_message(self, client, message, role='user'):
        """
            Add a Message to a Thread
        """
        self.log(message, self.author_id, role=role)
        try:
            message = client.beta.threads.messages.create(
                thread_id=self.thread,
                role="user",
                content=message
            )
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: Thread The server could not be reached {e.__cause__}")
            self.log(f"OPENAI: Thread The server could not be reached {e.__cause__}", status_code=e.status_code,
                     role='openai')
        except openai.RateLimitError as e:
            _logger.warning(f"OPENAI: Thread Ratelimit {e.status_code} {e.response}")
            self.log(f"OPENAI: Thread Ratelimit {e.status_code} {e.response}", status_code=e.status_code, role='openai')
        except openai.APIStatusError as e:
            _logger.warning(f"OPENAI: Thread Status error {e.status_code} {e.response}")
            self.log(f"OPENAI: Thread Status error {e.status_code} {e.response}", status_code=e.status_code,
                     role='openai', )

    def openai_wait4response(self, client):
        # TODO log does not save the correct author (it should be AI-bot)
        if self.run:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=self.thread,
                run_id=self.run
            )
            if run_status.status == 'expired':
                self.run = None
                self.log(f"OPENAI: Status error run expired {self.run=} {run_status.status=}",
                         self.recipient_id.parent_id, status_code=400, role='openai', )
        else:
            _logger.warning(f"Run saved {self.run=}")
            self.run = None

        _logger.warning(f"if not Run {self.run=} {self.thread=} {self.assistant=}")
        if not self.run:
            try:
                run = client.beta.threads.runs.create(
                    thread_id=self.thread,
                    assistant_id=self.assistant,
                    instructions=f"Please address the user as {self.author_id.name}."
                )
                self.log(run.model_dump_json(indent=4), self.recipient_id.parent_id, role='run')
                _logger.warning(f"Model_dump: {run.model_dump_json(indent=4)} {run.id=}")
                self.run = run.id
            except openai.APIConnectionError as e:
                _logger.warning(f"OPENAI: Run The server could not be reached {e.__cause__}")
                self.log(f"OPENAI: Run The server could not be reached {e.__cause__}", self.recipient_id.parent_id,
                         status_code=e.status_code, role='openai')
                return []
            except openai.RateLimitError as e:
                _logger.warning(f"OPENAI: Run Ratelimit {e.status_code} {e.response}")
                self.log(f"OPENAI: Run Ratelimit {e.status_code} {e.response}", self.recipient_id.parent_id,
                         status_code=e.status_code, role='openai')
                return []
            except openai.APIStatusError as e:
                _logger.warning(f"OPENAI: Run Status error {e.status_code} {e.response}")
                self.log(f"OPENAI: Run Status error {e.status_code} {e.response}", self.recipient_id.parent_id,
                         status_code=e.status_code, role='openai', )
                return [{'role': 'assistant', 'content': f"OPENAI: Status error {e.status_code} {e.response}"}]

        msgs = []
        while True:
            # Wait for 5 seconds
            # Retrieve the run status
            run_status = client.beta.threads.runs.retrieve(
                thread_id=self.thread,
                run_id=self.run
            )

            self.log(f"{run_status.status=}", self.recipient_id.parent_id, role='run', )
            # If run is completed, get messages
            if run_status.status == 'completed':
                messages = client.beta.threads.messages.list(
                    thread_id=self.thread
                )
                _logger.warning(f"Completed: {messages=}")

                # Loop through messages and print content based on role
                for msg in messages.data:
                    self.log(' '.join([m.text.value for m in msg.content]), self.recipient_id.parent_id, msg.role)
                    if msg.run_id == self.run:
                        msgs.append({'role': msg.role, 'content': msg.content[0].text.value})
                    _logger.warning(f"{msg.id=} {msg.run_id=} {msg.role=}: {msg.content=}")
                self.run = None
                break
            elif run_status.status == 'requires_action':

                _logger.warning(f"Function calling")

                required_actions = run_status.required_action.submit_tool_outputs.model_dump()
                _logger.warning(f"{run_status.status=} {required_actions}")
                self.log(f"{run_status.status=} {required_actions=}", self.recipient_id.parent_id, role='run')
                tool_outputs = []

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
                self.log(f"Submit tools outputs {tool_outputs=}", self.recipient_id.parent_id, role='run')
                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=self.thread,
                    run_id=self.run,
                    tool_outputs=tool_outputs
                )

            else:
                _logger.warning(f"Waiting for the Assistant to process... {run_status.status=} {self.run=}")

            time.sleep(1)
        return msgs

    def _unlink_thread(self, client, channel):
        try:
            client.beta.assistants.delete(self.assistant)
        except openai.APIConnectionError as e:
            _logger.warning(f"OPENAI: Delete The server could not be reached {e.__cause__}")
        except openai.RateLimitError as e:
            _logger.warning(f"OPENAI: Delete Ratelimit {e.status_code} {e.response}")
        except openai.APIStatusError as e:
            _logger.warning(f"OPENAI: Delete Status error {e.status_code} {e.response}")
        self.recipient_id.openai_assistant = None

    def get_stock_price(self, symbol: str) -> float:
        stock = yf.Ticker(symbol)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return price
