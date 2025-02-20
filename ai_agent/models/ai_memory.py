import json
import logging
import io
import pymupdf
import base64
import uuid
import faiss
import asyncio
import requests
import markdownify
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents.base import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from random import randint
from urllib.parse import urljoin, urlparse
from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError
from langchain_huggingface import HuggingFaceEmbeddings

_logger = logging.getLogger(__name__)


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
    _inherit = ["mail.thread", "mail.activity.mixin", ]
    _description = 'AI Memory'

    ai_agent_count = fields.Integer(compute="compute_ai_agent_count")
    ai_agent_ids = fields.One2many(comodel_name="ai.agent.memory", inverse_name="ai_memory_id")
    ai_agent_llm_id = fields.Many2one(
        string="Embedded LLM", comodel_name="ai.agent.llm", required=True,
        domain="[('is_embedded','=',True),('status','=','confirmed')]")
    attachment_ids = fields.Many2many(comodel_name="ir.attachment")
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    field_list = fields.Text(string='Field List', default="['name']", readonly=False)
    filter_domain = fields.Char(string='Record selection', )
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    max_nbr_pages = fields.Integer(string="Max Number of Pages")
    memory_faiss = fields.Binary(string='FAISS Index', attachment=True)
    memory_markdown = fields.Binary(string='Markdown', attachment=True)
    memory_type = fields.Selection(
        selection=[("bs4", "Simple Webscraper"), ("model", "Model"), ("local_attachment", "Local Attachment"),
                   ("attachments", "Attachments")], default="model", required=True,
        help="This is the source for memory")
    model_id = fields.Many2one(comodel_name='ir.model')
    model_name = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)
    name = fields.Char(required=True)
    nbr_days = fields.Integer(string='Number days this memory will live')
    object_id = fields.Reference(string='Object', selection=lambda m: [(model.model, model.name) for model in
                                                                       m.env['ir.model'].sudo().search([])])
    quest_count = fields.Integer(compute="compute_quest_count")
    session_count = fields.Integer(compute="compute_session_count")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_memory_id")
    split_chunk_overlap = fields.Integer(default=200)
    split_chunk_size = fields.Integer(default=1000)
    status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")], default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    url = fields.Char(string='Url', trim=True, )
    vector_type = fields.Selection(selection=[('faiss', 'FAISS'), ('st', 'Short Term')], string='Vector type',
                                   help="The type of vector database")

    def action_get_quests(self):
        action = {
            'name': 'AI Quests',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_memory_id", '=', self.id)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_memory_id", '=', self.id)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_memory_id", '=', self.id)],
        }
        return action

    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form',
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

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if self.model_id:
            self.field_list = "[" + ' ,'.join([f"'{f}'" for f in self.env['ir.model.fields'].search(
                [('model', '=', self.model_id.model)]).mapped('name')]) + "]"
        else:
            self.field_list = "[]"

    def rag_local_attatchemts(self):
        for memory in self:
            raw_documents = [self.create_document_from_file(attachment) for attachment in
                             self.env["ir.attachment"].search(
                                 [("res_model", "=", memory._name), ("res_id", "=", memory.id)])]
            if raw_documents:
                self.create_vector(raw_documents)
            else:
                raise UserError(_("No attachments to RAG"))

    def rag_attatchemts(self):
        for memory in self:

            raw_documents = [self.create_document_from_file(attachment) for attachment in
                             self.env["ir.attachment"].search(
                                 [("res_model", "=", memory._name), ("res_id", "=", memory.id)])]
            if raw_documents:
                self.create_vector(raw_documents)
            else:
                raise UserError(_("No attachments to RAG"))

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

    def run(self):
        for memory in self:
            if memory.status != "active":
                raise UserError(_(f"Wrong state on memory ({self.name})"))

            memory.last_run = fields.Datetime.now()
            if memory.memory_type == 'bs4':
                if not memory.url:
                    raise UserError(_(f"Missing url on memory ({self.name})"))
                all_pages = self.scrape_website(memory.url, memory.max_nbr_pages)
                memory.memory_markdown = base64.b64encode(all_pages)
                raw_documents = [memory.create_document(text=all_pages, metadata={})]
                memory.create_vector(raw_documents)
            elif memory.memory_type == 'model':
                model_fields = eval(memory.field_list)
                domain = safe_eval(memory.filter_domain) if memory.filter_domain else []
                module_dicts = memory.env[memory.model_name].search(domain).read(model_fields)
                _logger.error(f"{module_dicts=}")
                raw_documents = []
                for module_dict in module_dicts:
                    for key, item in module_dict.items():
                        if isinstance(item, fields.datetime):
                            module_dict[key] = item.isoformat()
                        if isinstance(item, bytes):
                            module_dict[key] = base64.b64encode(item).decode("utf-8")
                    raw_documents.append(memory.create_document(text=json.dumps(module_dict), metadata=module_dict))
                if len(raw_documents) != 0:
                    self.create_vector(raw_documents)
            elif memory.memory_type == 'attachments':
                memory.rag_attatchemts()
            elif memory.memory_type == 'local_attachment':
                memory.rag_local_attatchemts()

    def load_faiss(self):
        if self.memory_faiss:
            faiss_file = base64.b64decode(self.memory_faiss)
            # db = FAISS.deserialize_from_bytes(faiss_file,eval(self.ai_agent_llm_id.get_embedding()), allow_dangerous_deserialization=True)
            db = FAISS.deserialize_from_bytes(faiss_file, self.ai_agent_llm_id.get_embedding(),
                                              allow_dangerous_deserialization=True)
            return db
        else:
            return False

    def text_splitter(self, documents):
        return RecursiveCharacterTextSplitter(
            chunk_size=self.split_chunk_size,  # chunk size (characters)
            chunk_overlap=self.split_chunk_overlap,  # chunk overlap (characters)
            add_start_index=True,  # track index in original document
        ).split_documents(documents)

    def create_document(self, text, metadata):
        return Document(id=uuid.uuid4(), page_content=text, metadata=metadata)

    def create_document_from_file(self, attachment_id):
        file = base64.b64decode(attachment_id.datas)
        if attachment_id.name.split(".")[-1] == "pdf":
            pages = pymupdf.open(stream=file)
            content = "\n".join([page.get_text() for page in pages])
        else:
            content = file.decode("utf-8")
        return Document(id=uuid.uuid4(), page_content=f"{content}",
                        metadata={"name": attachment_id.name, "type": "attachment"})

    def create_vector(self, raw_documents):
        if self.vector_type == 'faiss':
            documents = self.text_splitter(raw_documents)
            embeddings = self.ai_agent_llm_id.get_embedding()
            self.test_embedd(embeddings)
            db = FAISS.from_documents(documents, embeddings)
            self.memory_faiss = base64.b64encode(db.serialize_to_bytes())

    def test_embedd(self, embeddings):
        try:
            embeddings.embed_query("test")
        except KeyError as e:
            _logger.error(f"{e=}")
            raise UserError("The embedding is not working. Please make sure you have the correct API key.")
        except Exception as e:
            _logger.error(f"{e=}")
            raise UserError(f"The embedding is not working and gave this error {e}")

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

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

    def cron(self):
        self.env['ai.memory'].search(
            [('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
