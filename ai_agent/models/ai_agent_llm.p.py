import httpx
import importlib
import logging
from collections import defaultdict
import time
import traceback
import tiktoken
import threading
import time

from httpx import HTTPStatusError
from langchain.agents import AgentExecutor, create_openai_tools_agent, create_json_chat_agent, create_react_agent
from langchain.schema import HumanMessage
from langchain_core.messages import AIMessage
from odoo import models, fields, api, tools, _
from odoo.exceptions import UserError, AccessError, ValidationError
from pydantic import SecretStr
from random import randint
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
_logger = logging.getLogger(__name__)

LICENCES = [
    ('ai-sweden-llm-ai-model', "AI Sweden's LLM AI Model License Agreement"),
    ('apache-2.0', 'Apache 2.0 License'),
    ('bigcode-open-rail-m-v1', 'BigCode Open RAIL-M v1 License Agreement'),
    ('commercial', 'Commercial License'),
    ('gemma-terms-of-use', 'Gemma Terms of Use'),
    ('google-ai-terms', 'Google AI-terms'),
    ('llama-community', 'Llama Community License'),
    ('Ilama3.1',"Llama 3.1 Community License"),
    ('Ilama3.3',"Llama 3.3 Community License"),
    ('mistral-research', 'Mistral Research License'),
    ('mit', 'MIT License'),
    ('deepseek','DEEPSEEK LICENSE AGREEMENT'),
    ('nvidia','NVIDIA Open Model License Agreement'),
    ('gemma','Gemma Terms of Use'),
    ('stability-ai','Stability AI CreativeML Open RAIL++-M'),
]

# In-memory storage for rate limiting
_rate_limit_lock = threading.RLock()
_token_usage = defaultdict(int)  # llm_id -> current tokens used
_request_usage = defaultdict(int)  # llm_id -> current requests
_usage_timestamps = {}  # llm_id -> last reset timestamp
_minute_buckets = {}                 # llm_id -> current minute bucket


class AIAgentLLM(models.Model):
    _name = 'ai.agent.llm'
    _description = 'AI Agent LLM'
    _inherit = ["mail.thread", "mail.activity.mixin"]

    ai_agent_count = fields.Integer(compute="compute_ai_agent_count")
    ai_agent_ids = fields.One2many(comodel_name="ai.agent", inverse_name="ai_agent_llm_id")
    ai_api_key = fields.Char(default=lambda self: self.product_tmpl_id.ai_api_key)
    color = fields.Integer(default=lambda self: randint(1, 11))
    company_id = fields.Many2one(comodel_name='res.company',string="Company",help="",related="product_tmpl_id.company_id") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    endpoint = fields.Char()
    image_128 = fields.Image("Image", max_width=128, max_height=128, related="product_tmpl_id.image_128")
    is_embedded = fields.Boolean(related='model_id.product_attribute_value_id.is_embedded')
    is_text2image = fields.Boolean(related='model_id.product_attribute_value_id.is_text2image')
    is_vision = fields.Boolean(related='model_id.product_attribute_value_id.is_vision')
    has_endpoint = fields.Boolean(related='model_id.product_attribute_value_id.has_endpoint')
    is_favorite = fields.Boolean()
    is_key_required = fields.Boolean(default=True)
    last_run = fields.Datetime()
    licence = fields.Selection(selection=LICENCES, string='Licence',
                               related='model_id.product_attribute_value_id.licence')
    llm_etype = fields.Char(related="product_tmpl_id.llm_etype")
    llm_type = fields.Char(related="product_tmpl_id.llm_type", required=True)
    model_id = fields.Many2one(
        comodel_name='product.template.attribute.value', string="Model", required=True, readonly=True
    )
    name = fields.Char(required=True)
    product_tmpl_id = fields.Many2one(comodel_name='product.template', string="Provider",
                                      domain="[('is_llm','=',True)]", required=True)
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_llm_id")
    status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        default="not_confirmed")
    status_color = fields.Integer(compute="compute_status_color")
    ## if VERSION >= '16.0'
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    ##  endif
    api_version = fields.Char(string="API version")

    tpm = fields.Integer(string="Token Per Minute", related="model_id.tpm")
    rpm = fields.Integer(string="Request Per Minute", related="model_id.rpm")
    threshold = fields.Float(string="Threshold", default=80)
    sleep_duration = fields.Integer(string="Sleep For", default=15)

    context_window = fields.Integer(
        string="Context Window", copy=False, related="model_id.context_window")
    has_temperature = fields.Boolean(
        string="Has Temperature", default=False, copy=False, related="model_id.has_temperature")

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            # #if VERSION >= "18.0"
            'view_mode': 'kanban,list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'kanban,tree,form',
            # #endif
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            # #if VERSION >= "18.0"
            'view_mode': 'kanban,list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'kanban,tree,form',
            # #endif            
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            # #if VERSION >= "18.0"
            'view_mode': 'list,form,calendar,pivot',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,form,calendar,pivot',
            # #endif    
            'target': 'current',
            'domain': [("ai_llm_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            # #if VERSION >= "18.0"
            'view_mode': 'list,form',
            # #elif VERSION <= "17.0"
            'view_mode': 'tree,form',
            # #endif    
            'target': 'current',
            'domain': [("session_line_ids.ai_llm_id", '=', self.id)]
        }
        return action

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])

    @api.depends("session_line_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_llm_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_llm_id.id == record.id).mapped('ai_quest_id')))

    @api.depends("ai_agent_ids")
    def compute_ai_agent_count(self):
        for record in self:
            record.ai_agent_count = len(record.ai_agent_ids)

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

    def get_llm(self, verbose=False, temperature=0.7, callbacks=None, **kwarg):
        try:
            module = importlib.import_module(self.product_tmpl_id.llm_library)
            LLM = getattr(module, self.product_tmpl_id.llm_type)

            api_key = self.ai_api_key
            if not api_key:
                api_key = tools.config.get(self.product_tmpl_id.fallback_api_key_name, False)
                _logger.error(f"{'API Key Found' if api_key else 'No API Key found'}")

            if self.product_tmpl_id.llm_type == "ChatOllama":
                kwarg["disable_streaming"] = True
            elif self.product_tmpl_id.llm_type == "AzureChatOpenAI":
                kwarg['api_version'] = self.api_version
                kwarg['azure_endpoint'] = self.endpoint
            else:
                kwarg["api_key"] = api_key

                if self.has_endpoint:
                    kwarg['base_url'] = self.endpoint

                if self.has_temperature:
                    kwarg['temperature'] = temperature
            
            return LLM(verbose=verbose, callbacks=callbacks, model=self.model_id.name, **kwarg)
        except ImportError as e:
            _logger.error(f"Error importing {self.product_tmpl_id.llm_library}: {e}")
            raise
        except AttributeError as e:
            _logger.error(f"Error: {self.product_tmpl_id.llm_type} not found in {self.product_tmpl_id.llm_library}")
            raise
        except Exception as e:
            _logger.error(f"An error occurred: {e}")
            raise

    def get_embedding(self):
        try:
            module = importlib.import_module(self.product_tmpl_id.llm_library)
            LLM = getattr(module, self.product_tmpl_id.llm_etype)
            api_key = self.ai_api_key
            if not api_key:
                api_key = tools.config.get(self.product_tmpl_id.fallback_api_key_name, False)
            if "HuggingFaceInferenceAPIEmbeddings" == self.product_tmpl_id.llm_etype:
                return LLM(api_key=SecretStr(api_key), model_name=self.model_id.name)
            elif "HuggingFaceEmbeddings" == self.product_tmpl_id.llm_etype:
                 return LLM(model_name=self.model_id.name)
            elif "OllamaEmbeddings" == self.product_tmpl_id.llm_etype:
                return LLM(model=self.model_id.name, base_url=self.endpoint)
            elif api_key:
                return LLM(api_key=api_key, model=self.model_id.name)
            return None

        except ImportError as e:
            _logger.error(f"Error importing {self.product_tmpl_id.llm_library}: {e}")
            raise
        except AttributeError as e:
            _logger.error(f"Error: {self.product_tmpl_id.llm_etype} not found in {self.product_tmpl_id.llm_library}")
            raise
        except Exception as e:
            _logger.error(f"An error occurred: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError)
    )
    def invoke_llm_with_retry(llm, messages):
        try:
            return llm.invoke(messages)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                _logger.warning(f"Rate limit exceeded. Retrying in a moment...")
                raise  # This will trigger a retry
            else:
                raise  # For other HTTP errors, don't retry

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError)
    )
    def invoke(self, input_text, config=None, session=None, quest=None, agent=None, debug=False):
        if input_text is None:
            error_msg = f"Input cannot be None. Please provide a valid input. {input_text=} {config=} {session=} {quest=} {agent=}"
            self.log_message(body=error_msg, is_error=True)
            raise ValueError(error_msg)
        try:
            response = self.get_llm().invoke(input_text, config)
        except HTTPStatusError as e:
            if e.response.status_code == 429:
                _logger.warning(f"Rate limit exceeded. Retrying in a moment...")
                self.log_message(body=f"Rate limit exceeded. Retrying in a moment...\n{input_text=} {config=} {session=} {quest=} {agent=}", is_error=False)
                raise  # This will trigger a retry
            else:
                _logger.warning(f"Other HTTP-error... {e=}")
                self.log_message(body=f"Other HTTP-error...{e=}\n{input_text=} {config=} {session=} {quest=} {agent=}", is_error=True)
                raise  # For other HTTP errors, don't retry
        except Exception as e:
            self.log_message(body=f"LLM {self.name} {e}\n\n{input=} {config=} {session=} {quest=} {agent=}\n{traceback.format_exc()}", is_error=True)
            _logger.error(f"LLM {self.name} {e}\n{traceback.format_exc()}")
            return None

        content = response.content
        if response and session:
            session.save_messages(response)
        if debug:
            self.log_message(body=f"LLM {self.name} {response=}", is_error=False)
        return response

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "not_confirmed":
                record.status_color = 3  # Orange
            elif record.status == "confirmed":
                record.status_color = 10  # Green
            elif record.status == "error":
                record.status_color = 1  # Red

    def test_llm(self):
        session = self.env['ai.quest.session'].llm_init(self)
        if self.is_embedded:
            return self.test_embedd(session)          
        else:
            try:
                response = self.invoke("What is 1+1, answer with a single digit")
            except ModuleNotFoundError as e:
                raise UserError(f"{e}")
            except Exception as e:
                _logger.error(f"{e=}")
                session.add_message(f"Could not confirm llm: {str(e)}\n{traceback.format_exc()}")
                self.message_post(body=_(f"Could not confirm llm: {str(e)}"), message_type="notification")
                self.status = "error"
                session.status = 'done'
                return False
            session.status = 'done'
            if isinstance(response, AIMessage):
                content = response.content.strip()
                if content == "2":
                    self.message_post(body=_(f"Llm confirmed: 1+1={content}"), message_type="notification")
                    self.status = "confirmed"
                    return 
        session.status = 'done'
        self.message_post(body=_(f"Could not confirm llm: {response=}"), message_type="notification")

    def test_embedd(self,session=False):
        try:
            self.get_embedding().embed_query("test")
        except ModuleNotFoundError as e:
            raise UserError(f"{e}")
        except KeyError as e:
            _logger.error(f"{e=}")
            session.add_message(f"Could not embedd: {str(e)}\n{traceback.format_exc()}")
            self.message_post(body=_(f"Could not embedd: {str(e)}"), message_type="notification")
            self.status = "error"
            if session:
                session.status = 'done'
            return False
        except Exception as e:
            _logger.error(f"{e=}")
            session.add_message(f"Could not embedd: {str(e)}\n{traceback.format_exc()}")
            self.message_post(body=_(f"Could not embedd: {str(e)}"), message_type="notification")
            self.status = "error"
            if session:
                session.status = 'done'
            return False
        self.message_post(body=_(f"Embedding is working"), message_type="notification")
        self.status = "confirmed"
        return 


    def get_agent_executor(self, prompt, tools, temperature=1.0, verbose=False, callbacks=None):
        return AgentExecutor(
            agent=create_openai_tools_agent(
                eval(
                    self.get_llm(temperature=temperature, verbose=verbose)
                ), tools, prompt
            ),
            tools=tools,
            verbose=verbose,
            callbacks=callbacks,
        )

    def update_api_key(self):
        for llm in self:
            llm.ai_api_key = llm.product_tmpl_id.ai_api_key

    def _compute_tokens(self, text):
        """Count tokens accurately using tiktoken for OpenAI models."""
        try:
            # Convert to string if needed
            if not isinstance(text, str):
                if hasattr(text, 'content'):
                    text = text.content
                else:
                    text = str(text)

            enc = tiktoken.encoding_for_model(self.model_id.name)
            token_count = len(enc.encode(text))
            return token_count
        except Exception as e:
            _logger.warning(f"Error using tiktoken: {e}. Using character-based token estimation.")
            return len(text) // 4 if text else 0  # Rough estimate of 4 chars per token


    def _reset_if_new_minute(self):
        """Reset counters if we've moved to a new minute"""
        current_minute = int(time.time()) // 60

        with _rate_limit_lock:
            last_minute = _minute_buckets.get(self.id, 0)
            if current_minute > last_minute:
                # Reset counters for this LLM
                _token_usage[self.id] = 0
                _request_usage[self.id] = 0
                _minute_buckets[self.id] = current_minute
                _logger.info(f"Rate limiting counters reset for LLM {self.name} (ID: {self.id})")
                return True
        return False

    def _check_rpm_limits(self):
        """Check request per minute limits"""
        current_requests = _request_usage.get(self.id, 0)

        # Check if would exceed RPM limit
        if 0 < self.rpm < current_requests + 1:
            error_msg = f"Request per minute (RPM) limit exceeded for {self.name}: {current_requests + 1} > {self.rpm}"
            _logger.warning(error_msg)
            raise UserError(error_msg)

        # Check if approaching threshold
        at_request_threshold = self.rpm > 0 and current_requests > (self.rpm * self.threshold / 100)
        if at_request_threshold:
            _logger.info(f"RPM threshold reached for {self.name}: {current_requests}/{self.rpm}")
            _logger.info(f"Sleeping for {self.sleep_duration}s to avoid exceeding RPM limits")
            time.sleep(self.sleep_duration)

        # Increment request counter
        _request_usage[self.id] += 1
        _logger.debug(f"Request count for {self.name} (ID: {self.id}) increased to {_request_usage[self.id]}")

        return True

    def _check_tpm_limits(self, input_text):
        """Check token per minute limits"""
        if not input_text or self.tpm <= 0:
            return True

        # Always reset if we've moved to a new minute before checking
        self._reset_if_new_minute()

        # Get CURRENT token usage AFTER potential reset
        current_tokens = _token_usage.get(self.id, 0)
        estimated_tokens = self._compute_tokens(input_text)

        # Log the current state for debugging
        _logger.info(
            f"TPM check: current_tokens={current_tokens}, estimated_tokens={estimated_tokens}, limit={self.tpm}")

        # Check if single request is too large
        if estimated_tokens > self.tpm:
            error_msg = f"Single request TPM exceeds limit for {self.name}: {estimated_tokens} > {self.tpm}"
            _logger.warning(error_msg)
            raise UserError(error_msg)

        # If accumulated tokens would exceed limit, sleep
        if current_tokens + estimated_tokens > self.tpm:
            _logger.info(f"Would exceed TPM limit: {current_tokens} + ~{estimated_tokens} > {self.tpm}")
            _logger.info(f"Sleeping for {self.sleep_duration}s to slow down token usage")
            time.sleep(self.sleep_duration)

            # Update token count after sleeping
            self._reset_if_new_minute()
            current_tokens = _token_usage.get(self.id, 0)

        # Check if approaching threshold - only if we already have some accumulated tokens
        elif current_tokens > 0 and (current_tokens + estimated_tokens) > (self.tpm * self.threshold / 100):
            _logger.info(f"TPM threshold reached for {self.name}: {current_tokens + estimated_tokens}/{self.tpm}")
            _logger.info(f"Sleeping for {self.sleep_duration}s to avoid exceeding TPM limits")
            time.sleep(self.sleep_duration)

        # Increment token counter
        _token_usage[self.id] += estimated_tokens
        _logger.debug(f"Input token usage for {self.name} (ID: {self.id}) increased to {_token_usage[self.id]}")

        return True

    def check_rate_limits(self, input_text=None):
        """
        Check both RPM and TPM limits before making a request
        Applies sleep automatically if approaching thresholds

        Args:
            input_text: Optional text to estimate token count for

        Returns:
            Boolean: True if the request can proceed

        Raises:
            UserError if limits would be exceeded
        """
        # Skip if no limits defined
        if not self.rpm and not self.tpm:
            return True

        # Reset counters if in a new minute
        self._reset_if_new_minute()

        with _rate_limit_lock:
            _logger.info(
                f"Current token usage before checks: {_token_usage.get(self.id, 0)}/{self.tpm}, current RPM: {_request_usage.get(self.id, 0)}/{self.rpm}")

            # Check RPM limits first
            self._check_rpm_limits()

            # Then check TPM limits if input text provided
            if input_text and self.tpm > 0:
                self._check_tpm_limits(input_text)

        return True

    def record_usage(self, tokens):
        """
        Record token usage from response metadata

        Args:
            tokens: Total tokens from response metadata
        """
        if not tokens:
            return

        # Reset counters if in a new minute
        self._reset_if_new_minute()

        with _rate_limit_lock:
            # Record tokens
            _token_usage[self.id] += tokens

            # Check if we've exceeded TPM (this is just for logging since we can't stop the request now)
            if self.tpm and 0 < self.tpm < _token_usage[self.id]:
                _logger.warning(
                    f"TPM limit exceeded for {self.name} after response: "
                    f"{_token_usage[self.id]} > {self.tpm}"
                )
            else:
                _logger.info(
                    f"Recorded {tokens} tokens for {self.name} (ID: {self.id}), "
                    f"now at {_token_usage[self.id]}/{self.tpm or 'unlimited'}"
                )
