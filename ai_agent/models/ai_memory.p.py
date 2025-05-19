from bs4 import BeautifulSoup
from langchain_core.documents.base import Document
from langchain_community.vectorstores import FAISS
from typing import Generator
from markupsafe import Markup
from langchain_community.document_loaders import PyPDFLoader
from random import randint
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from dateutil.relativedelta import relativedelta
from langchain_community.document_loaders import TextLoader
from urllib.parse import urljoin
from urllib.parse import urlparse
import base64
import datetime
import faiss
import io
import json
import logging
import markdownify
import math
import requests
import subprocess
import tiktoken
import time
import uuid
from secrets import choice

# #if VERSION >= "16.0"
import pymupdf
# #elif VERSION <= "15.0"
import fitz as pymupdf
# #endif

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


avatar_memory = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 530.06 530.06">
<circle cx="265.03" cy="265.03" r="265.03" fill="#875a7b"/>
<path d="M371.04 212.02H159.02c-14.58 0-26.41 11.83-26.41 26.41v132.52c0 14.58 11.83 26.41 26.41 26.41h212.02c14.58 0 26.41-11.83 26.41-26.41V238.43c0-14.58-11.83-26.41-26.41-26.41zm0 158.93H159.02V238.43h212.02v132.52z" fill="#ffffff"/>
<circle cx="212.02" cy="291.44" r="26.41" fill="#ffffff"/>
<circle cx="318.04" cy="291.44" r="26.41" fill="#ffffff"/>
<path d="M265.03 132.52c-29.15 0-52.81 23.66-52.81 52.81v26.41h105.62v-26.41c0-29.15-23.66-52.81-52.81-52.81zm0 52.81c-14.58 0-26.41-11.83-26.41-26.41s11.83-26.41 26.41-26.41 26.41 11.83 26.41 26.41-11.83 26.41-26.41 26.41z" fill="#ffffff"/>
<rect x="238.62" y="344.25" width="52.81" height="26.41" fill="#ffffff"/>
</svg>'''


class AIAgentMemory(models.Model):
    _name = 'ai.agent.memory'
    _description = 'AI Agent Memory'

    ai_agent_id = fields.Many2one(comodel_name='ai.agent', string="", help="")
    sequence = fields.Integer(string='Sequence')
    nbr_days = fields.Integer(string='Number days this memory will live', related="ai_memory_id.nbr_days")
    last_run = fields.Datetime(string='Last Run', related="ai_memory_id.last_run")
    ai_memory_id = fields.Many2one(comodel_name='ai.memory', string="Memory", help="")
    ai_memory_status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        related="ai_memory_id.status")
    ai_memory_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM",
                                       related="ai_memory_id.ai_agent_llm_id")
    ai_memory_llm_status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        related="ai_memory_id.ai_agent_llm_id.status")
    ai_memory_url = fields.Char(string='Url', related="ai_memory_id.url")

    def run(self):
        self.ai_memory_id.run()

    def get_memory(self,**kwarg):
        rag = ""
        for m in self:
            rags=[]
            for q in ['question', 'message', 'latest_message', 'topic']:
                if q in kwarg:
                    rags.extend(m.ai_memory_id._get_memory(kwarg[q], m.ai_memory_id.nbr_rags))
            rag += f"Memory: {m.ai_memory_id.name} instructions: {m.ai_memory_id.instruction}\n" + "\n".join(list(set(rags))[:m.ai_memory_id.nbr_rags])
        return rag




class AIquestMemory(models.Model):
    _name = 'ai.quest.memory'
    _description = 'AI Quest Memory'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest', string="", help="")
    sequence = fields.Integer(string='Sequence')
    nbr_days = fields.Integer(string='Number days this memory will live', related="ai_memory_id.nbr_days")
    last_run = fields.Datetime(string='Last Run', related="ai_memory_id.last_run")
    ai_memory_id = fields.Many2one(comodel_name='ai.memory', string="Memory", help="")
    ai_memory_status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        related="ai_memory_id.status")
    ai_memory_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM",
                                       related="ai_memory_id.ai_agent_llm_id")
    ai_memory_llm_status = fields.Selection(
        selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],
        related="ai_memory_id.ai_agent_llm_id.status")
    ai_memory_url = fields.Char(string='Url', related="ai_memory_id.url")

    def run(self):
        self.ai_memory_id.run()


class AIMemory(models.Model):
    _name = 'ai.memory'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    #_inherit = ["mail.thread", "mail.activity.mixin", "llm.embedding.mixin"]
    _description = 'AI Memory'


    # #if VERSION == "14.0"
    avatar_128 = fields.Image("Avatar", max_width=128, max_height=128)
    # #elif VERSION >= "15.0"
    avatar_128 = fields.Image("Avatar", max_width=128, max_height=128, compute='_compute_avatar_128')
    # #endif



    ai_agent_count = fields.Integer(compute="compute_ai_agent_count")
    ai_agent_ids = fields.One2many(comodel_name="ai.agent.memory", inverse_name="ai_memory_id")
    ai_agent_llm_id = fields.Many2one(
        string="Embedded LLM", comodel_name="ai.agent.llm", required=False,
        domain="[('is_embedded','=',True),('status','=','confirmed')]")
    attachment_ids = fields.Many2many(comodel_name="ir.attachment")
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    company_id = fields.Many2one(comodel_name='res.company',string="Company",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    debug = fields.Boolean(string='Debug')
    field_list = fields.Text(string='Field List', default="['name']", readonly=False)
    filter_domain = fields.Char(string='Record selection')
    image_128 = fields.Image(string="Image", max_width=128, max_height=128)
    instruction = fields.Text(string="Instruction",help="Instructions how the LLM should use this memory")
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    max_nbr_pages = fields.Integer(string="Max Number of Pages")
    memory_faiss = fields.Binary(string='FAISS Index', attachment=True, copy=False)
    memory_markdown = fields.Binary(string='Markdown', attachment=True)
    memory_type = fields.Selection(
        selection=[("bs4", "Simple Webscraper"), ("model", "Model"), ("local_attachment", "Local Attachment"),
                   ("attachments", "Attachments"),("datastream", "Datastream")], default="model", required=True,
        help="This is the source for memory")
    model_id = fields.Many2one(comodel_name='ir.model')
    model_name = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)
    name = fields.Char(required=True)
    nbr_days = fields.Integer(string='Number days this memory will live')
    nbr_rags = fields.Integer(string="Number rags",default=3,help='Number rags this memory will add to LLM context')
    object_id = fields.Reference(string='Object', selection=lambda m: [(model.model, model.name) for model in
                                                                       m.env['ir.model'].sudo().search([])])
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_memory_id")
    shell_cmd  = fields.Text(string="Command",help="Shell command to obtain the datastream ge cat filename")
    split_chunk_overlap = fields.Integer(default=200)
    split_chunk_size = fields.Integer(default=1000)
    status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")], default="draft")
    status_color = fields.Integer(compute="compute_status_color")
    # #if VERSION >= "16.0"
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    # #endif
    url = fields.Char(string='Url', trim=True, )
    vector_type = fields.Selection(selection=[('faiss', 'FAISS'), ('st', 'Short Term')], string='Vector type',
                                   help="The type of vector database")
    document_chunks = fields.Integer(string="Chunks Per Session", default=0, help="This number will limit the amount of document chunks used in one embedding session. This can help with timeout issues. If zero, one session will be used.")

    @api.constrains('document_chunks')
    def _check_document_chunks(self):
        for record in self:
            if record.document_chunks < 0:
                raise ValidationError(_("The chuncks per session can't be less than zero."))

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if self.model_id:
            self.field_list = "[" + ' ,'.join([f"'{f}'" for f in self.env['ir.model.fields'].search(
                [('model', '=', self.model_id.model)]).mapped('name')]) + "]"
        else:
            self.field_list = "[]"

    @api.model
    def _generate_random_token(self):
        return ''.join(choice('abcdefghijkmnopqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ23456789') for _i in range(10))

    uuid = fields.Char('UUID', size=50, default=_generate_random_token, copy=False)

    # #if VERSION >= '16.0'
    @api.depends('memory_type', 'image_128', 'uuid')
    def _compute_avatar_128(self):
        for record in self:
            record.avatar_128 = record.image_128 or record._generate_avatar()

    def _generate_avatar(self):

        avatar = {
            'bs4': avatar_memory,
            'model': avatar_memory,
            'local_attachment': avatar_memory,
            'attachments': avatar_memory,
            'datastream': avatar_memory,
        }[self.memory_type]
        bgcolor = get_hsl_from_seed(self.uuid)
        avatar = avatar.replace('fill="#875a7b"', f'fill="{bgcolor}"')
        return base64.b64encode(avatar.encode())

    # #endif



    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "draft":
                record.status_color = 3  # Orange
            elif record.status == "active":
                record.status_color = 10  # Green
            elif record.status == "done":
                record.status_color = 3  # Orange
            elif record.status == "error":
                record.status_color = 1  # Red

    # ------------------------------------------------------------
    # RAG
    # ------------------------------------------------------------

    def rag_local_attachments(self,memory):
        documents = []
        attachments = self.env["ir.attachment"].search(
                                [("res_model", "=", memory._name), ("res_id", "=", memory.id)])
        for attachment in attachments:
            documents.extend(self.create_documents_from_file(attachment))

        if documents:
            self.create_vector(documents=documents,memory=memory)
        else:
            raise UserError(_("No attachments to RAG"))

    def rag_attachments(self,memory):
        if memory.attachment_ids:
            documents = []
            for attachment in memory.attachment_ids:
                documents.extend(self.create_documents_from_file(attachment))
            
            if documents:
                self.create_vector(documents=documents,memory=memory)
            else:
                raise UserError(_("No attachments to RAG"))


    def read_stream_chunked(self,command: str, chunk_size: int = 1024) -> Generator[str, None, None]:
        """
            Read datastream from Unix-command in chunks as a generator
        """
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, text=True)
        while True:
            chunk = process.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def action_test_rag(self):
        action = {
            'name': 'Test Action',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.memory.test.wizard',
            'view_mode': 'form',
            'context': {'default_ai_memory': self.id},
            'target': 'new'
        }
        return action

    def text_splitter(self, documents):
        return RecursiveCharacterTextSplitter(
            chunk_size=self.split_chunk_size,  # chunk size (characters)
            chunk_overlap=self.split_chunk_overlap,  # chunk overlap (characters)
            add_start_index=True,  # track index in original document
        ).split_documents(documents)

    def create_document(self, text, metadata):
        return Document(id=uuid.uuid4(), page_content=text, metadata=metadata)

    def create_documents_from_file(self, attachment_id):
        file = base64.b64decode(attachment_id.datas)
        documents = []
        if attachment_id.name.split(".")[-1] == "pdf":
            pages = pymupdf.open(stream=file)
            _logger.error(f"{pages=}")
            for page in pages:
                metadata = pages.metadata
                metadata.update({"name": attachment_id.name, "type": "pdf", "page_count": pages.page_count, "table_of_content": pages.get_toc()})
                documents.append(self.create_document(text=f"{page.get_text()}",metadata=metadata))
        else:
            content = file.decode("utf-8")
            documents.append(self.create_document(text=f"{content}",metadata={"name": attachment_id.name, "type": "text"}))
        return documents

    def setup_db_for_bs4(self,memory):
        if not memory.url:
            raise UserError(_(f"Missing url on memory ({self.name})"))
        all_pages = self.scrape_website(memory.url, memory.max_nbr_pages)
        memory.memory_markdown = base64.b64encode(all_pages)
        documents = [memory.create_document(text=all_pages, metadata={})]
        memory.create_vector(documents=documents,memory=memory)

    def _model_memory_type_data(self, memory):
        model_fields = eval(memory.field_list)
        domain = safe_eval(memory.filter_domain) if memory.filter_domain else []
        record_data = memory.env[memory.model_name].search(domain).read(model_fields)
        return record_data

    def setup_db_for_model(self, memory):
        module_dicts = self._model_memory_type_data(memory=memory)
        documents = []
        for module_dict in module_dicts:
            for key, item in module_dict.items():
                if isinstance(item, fields.datetime) or isinstance(item, fields.date):
                    module_dict[key] = item.isoformat()
                if isinstance(item, bytes):
                    module_dict[key] = base64.b64encode(item).decode("utf-8")
                if isinstance(item, fields.Html):
                    module_dict[key] = BeautifulSoup(item.encode('utf-8').decode('unicode_escape'),
                                                     'html.parser').get_text()
                if isinstance(item, Markup):
                    decoded = str(item).encode('utf-8').decode('unicode_escape')
                    # Step 3: Remove HTML
                    clean_text = BeautifulSoup(item, 'html.parser').get_text()
                    # ~ module_dict[key] = str(clean_text.encode('utf-8'))
                    module_dict[key] = str(clean_text)

                    _logger.warning(f"{clean_text}=  {repr(item)}=")

            documents.append(memory.create_document(text=json.dumps(module_dict), metadata=module_dict))
        self.create_vector(documents=documents, memory=memory)

    def _compute_tokens(self, text):
        """Count tokens accurately using tiktoken for OpenAI models."""
        try:

            # Convert to string if needed
            if not isinstance(text, str):
                if hasattr(text, 'content'):
                    text = text.content
                else:
                    text = str(text)

            enc = tiktoken.encoding_for_model(self.ai_agent_llm_id.model_id.name)

            token_count = len(enc.encode(text))
            return token_count
        except Exception as e:
            _logger.warning(f"Error using tiktoken: {e}. Using character-based token estimation.")
            return len(text) // 4 if text else 0


    def _get_current_minute_usage(self):
        """Calculate current minute token usage from session lines"""
        # Get current minute timestamp (rounded down)
        current_minute = int(time.time()) // 60

        minute_start = datetime.datetime.fromtimestamp(current_minute * 60)
        minute_end = datetime.datetime.fromtimestamp((current_minute + 1) * 60)

        # Find session lines for this LLM in the current minute
        domain = [
            ('ai_llm_id', '=', self.id),
            ('datetime', '>=', minute_start),
            ('datetime', '<', minute_end)
        ]

        # Sum the tokens
        token_sum = sum(self.env['ai.quest.session.line'].search(domain).mapped('token'))
        return token_sum


    # ------------------------------------------------------------
    # Create Vector Hook
    # ------------------------------------------------------------

    def create_vector(self, documents, memory):
        split_documents = self.text_splitter(documents)
        embeddings = self.ai_agent_llm_id.get_embedding()
        if not embeddings:
            raise UserError("No Embedding found")
        if self.vector_type == 'faiss':

            input_tokens = self._compute_tokens(split_documents)

            # Check current minute usage from session lines
            current_usage = self._get_current_minute_usage()

            if self.ai_agent_llm_id.tpm != 0 and current_usage + input_tokens > self.ai_agent_llm_id.tpm:
                message = f"TPM limit would be exceeded: {current_usage + input_tokens} > {self.ai_agent_llm_id.tpm}"
                _logger.warning(message)
                raise UserError(message)

                # Sleep until next minute
                # seconds_to_next_minute = 60 - (int(time.time()) % 60)
                # _logger.info(f"Sleeping for {seconds_to_next_minute} seconds until next minute")
                # time.sleep(seconds_to_next_minute)

            elif current_usage > (self.ai_agent_llm_id.tpm * self.ai_agent_llm_id.threshold / 100):
                _logger.warning(
                    f"TPM threshold reached: {current_usage}/{self.ai_agent_llm_id.tpm} tokens used this minute")
                time.sleep(self.ai_agent_llm_id.sleep_duration)  # Sleep for the configured duration (default 15 sec)
            
            if memory.document_chunks != 0:
                if len(split_documents) < memory.document_chunks:
                    raise UserError(f"The chunks per session ({memory.document_chunks}) is less than the number of documents after they have been split ({len(split_documents)}), which is not allowed. If you are using a model, a tip is to not set the chunks per session to be bigger than the amount of records you have.")
                runs = math.ceil(len(split_documents) / memory.document_chunks)
                self.memory_faiss = False
                for run in range(runs):
                    docs_to_embedd = split_documents[run * memory.document_chunks:(run + 1) * memory.document_chunks]
                    if not memory.memory_faiss:
                        db = FAISS.from_documents(docs_to_embedd, embeddings)
                      
                        self.memory_faiss = base64.b64encode(db.serialize_to_bytes())
                    else:
                        self.add_to_memory_faiss(docs_to_embedd)
            else:
                db = FAISS.from_documents(split_documents, embeddings)
                self.memory_faiss = base64.b64encode(db.serialize_to_bytes())

            if not self.memory_faiss:
                raise UserError(_("Faild to embedd"))

        return split_documents, embeddings
        
    # ------------------------------------------------------------
    # Get Memory Hook
    # ------------------------------------------------------------

    def _get_memory(self, question, k):
        rags = []
        for m in self:
            if m.vector_type == "faiss":
                db = m.load_faiss()
                if db:
                    for doc in db.similarity_search(question, k=k):
                        if doc and doc.page_content:
                           rags.append(doc.page_content)
        return rags


    # ------------------------------------------------------------
    # FAISS
    # ------------------------------------------------------------

    def add_to_memory_faiss(self,split_documents):
        db = self.load_faiss()
        db.add_documents(documents=split_documents)
        self.memory_faiss = base64.b64encode(db.serialize_to_bytes())

    def load_faiss(self):
        if self.memory_faiss:
            faiss_file = base64.b64decode(self.memory_faiss)
            db = FAISS.deserialize_from_bytes(faiss_file, self.ai_agent_llm_id.get_embedding(),
                                              allow_dangerous_deserialization=True)
            return db
        else:
            return False


    # ------------------------------------------------------------
    # Scrape Website
    # ------------------------------------------------------------

    def scrape_website(self, website, max_nbr_pages):
        self.ensure_one()
        global all_pages
        all_pages = ""

        def is_same_domain(url, domain):
            return urlparse(url).netloc == urlparse(domain).netloc

        def scrape_page(url, visited):
            global all_pages
            _logger.warning(f'scraping {url=} {visited=} {len(all_pages)=}')
            if url in visited:
                return
            if max_nbr_pages > 0 and len(visited) > max_nbr_pages:
                return
            visited.add(url)

            try:
                response = requests.get(url)
                soup = BeautifulSoup(response.content, 'html.parser')

                # Save page content as attachment
                content = soup.get_text()
                all_pages += f"### URL({url})\n" + markdownify.markdownify(content)

                # Follow links
                for link in soup.find_all('a', href=True):
                    next_url = urljoin(url, link['href'])
                    if is_same_domain(next_url, website):
                        scrape_page(next_url, visited)
            except Exception as e:
                _logger.error(f"Error scraping {url}: {str(e)}")

        visited_urls = set()
        scrape_page(website, visited_urls)
        return all_pages.encode('utf-8')


    # ------------------------------------------------------------
    # Model/CRUD
    # ------------------------------------------------------------

    def run(self):
        if self.debug:
            self.real_run()
        else:
            self.with_delay().real_run()

    def real_run(self):
        for memory in self:
            if memory.status != "active":
                raise UserError(_(f"Wrong state on memory ({self.name})"))

            memory.last_run = fields.Datetime.now()
            if memory.memory_type == 'bs4':
                memory.setup_db_for_bs4(memory)
            elif memory.memory_type == 'model':
                memory.setup_db_for_model(memory)
            elif memory.memory_type == 'attachments':
                memory.rag_attachments(memory)
            elif memory.memory_type == 'local_attachment':
                memory.rag_local_attachments(memory)
            elif memory.memory_type == 'datastream':
                memory.file_stream(memory)

    def file_stream(self,memory):
        documents = []
        for index, chunk in enumerate(self.read_stream_chunked(self.shell_cmd, self.split_chunk_size)):
            documents.append(self.create_document(text=chunk,
                metadata={"name": self.name, "type": "datastream", 'chunk_number': index + 1}))
        memory.create_vector(documents=documents,memory=memory)

    def cron(self):
        self.env['ai.memory'].search(
            ['&',('nbr_days','>',0),('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
    
    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

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
            'domain': [("session_line_ids.ai_memory_id", '=', self.id)]
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
            'domain': [("session_line_ids.ai_memory_id", '=', self.id)]
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
            'domain': [("ai_memory_id", '=', self.id)],
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
            'domain': [("session_line_ids.ai_memory_id", '=', self.id)]
        }
        return action

    @api.depends("model_id")
    def compute_model_id(self):
        for record in self:
            _logger.error(f"{record.model_id.model=}")
            record.compute_model_id = record.model_id

    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])

    @api.depends("session_line_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_memory_id.id == record.id).mapped(
                    'ai_quest_session_id')))

    @api.depends("session_line_ids")
    def compute_quest_count(self):
        for record in self:
            record.quest_count = len(
                set(record.session_line_ids.filtered(lambda x: x.ai_memory_id.id == record.id).mapped('ai_quest_id')))

    @api.depends("ai_agent_ids")
    def compute_ai_agent_count(self):
        for record in self:
            record.ai_agent_count = len(record.ai_agent_ids)

    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128
        
