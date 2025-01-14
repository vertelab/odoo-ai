import json
import logging
import io
import PyPDF2
import base64
import uuid
import faiss
import asyncio
import requests
import markdownify
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from langchain.chains import ConversationalRetrievalChain
from langchain_community.document_loaders import TextLoader
from langchain.memory import ConversationBufferMemory
from langchain.schema import SystemMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from random import randint
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents.base import Document
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from crawl4ai import AsyncWebCrawler
from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class AIAgentMemory(models.Model):
    _name = 'ai.agent.memory'
    _description = 'AI Agent Memory'

    ai_agent_id = fields.Many2one(comodel_name='ai.agent', string="", help="")
    sequence = fields.Integer(string='Sequence')
    nbr_days = fields.Integer(string='Number days this memory will live', related="ai_memory_id.nbr_days")
    last_run = fields.Datetime(string='Last Run', related="ai_memory_id.last_run")
    ai_memory_id = fields.Many2one(comodel_name='ai.memory', string="Memory", help="")
    ai_memory_status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],related="ai_memory_id.status")
    ai_memory_llm_is = fields.Many2one(comodel_name='',string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate  fields.Char(string='Url', related="ai_memory_id.url" )
    ai_memory_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", related="ai_memory_id.llm_id")
    ai_memory_llm_status = fields.Selection(selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],related="ai_memory_id.llm_id.status")
    ai_memory_url = fields.Char(string='Url', related="ai_memory_id.url" )

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
    ai_memory_status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],related="ai_memory_id.status")
    ai_memory_llm_is = fields.Many2one(comodel_name='',string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate  fields.Char(string='Url', related="ai_memory_id.url" )
    ai_memory_llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", related="ai_memory_id.llm_id")
    ai_memory_llm_status = fields.Selection(selection=[("not_confirmed", "Not Confirmed"), ("confirmed", "Confirmed"), ("error", "Error")],related="ai_memory_id.llm_id.status")
    ai_memory_url = fields.Char(string='Url', related="ai_memory_id.url" )

    def run(self):
        self.ai_memory_id.run()


class AIMemory(models.Model):
    _name = 'ai.memory'
    _inherit = ["mail.thread", "mail.activity.mixin", ]

    _description = 'AI Memory'

    memory_type = fields.Selection(selection=[("bs4", "Simple Webscraper"),("model","Model"),("module", "Module"),("local_attachment","Local Attachment"), ("object_attachment", "Object Attachment")],default="model", required=True, help="This is the source for memory")
    vector_type = fields.Selection(selection=[('faiss', 'FAISS'), ('st', 'Short Term')], string='Vector type', help="The type of vector database")
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Embedded Large Language Model",domain="[('status','=','confirmed')]")
    memory_faiss = fields.Binary(string='FAISS Index', attachment=True)
    memory_markdown = fields.Binary(string='Markdown', attachment=True)
    name = fields.Char(required=True)
    nbr_days = fields.Integer(string='Number days this memory will live')
    split_chunk_size = fields.Integer(default=1000)
    split_chunk_overlap = fields.Integer(default=200)
    ai_agent_llm_id = fields.Many2one(string="Embedded LLM", comodel_name="ai.agent.llm", required=True, domain="[('status','=','confirmed')]")
    status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    attachment_ids = fields.Many2many(comodel_name="ir.attachment")
    url = fields.Char(string='Url', trim=True, )
    model_id = fields.Many2one(comodel_name='ir.model')
    object_id = fields.Reference(string='Object',
                                selection=lambda m: [(model.model, model.name) for model in
                                                      m.env['ir.model'].sudo().search([])])


    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128

    def rag_attatchemts(self):
        for memory in self:
            raw_documents = [self.create_document_from_file(attachment) for attachment in 
                         self.env["ir.attachment"].search([("res_model", "=", memory._name), ("res_id", "=", memory.id)])]
            if raw_documents:
                self.create_faiss(raw_documents)
            else:
                raise UserError(_("No attachments to RAG"))
        
    def rag_models(self):
        for memory in self:
            model_dicts = memory.env["ir.model"].search([]).read(["name", "model", "info","modules"])
            if len(model_dicts) != 0: 
                raw_documents = [self.create_document(text=json.dumps(model_dict),metadata=model_dict) for model_dict in model_dicts]
                self.create_faiss(raw_documents)

    def rag_modules(self):
        for memory in self:
            module_dicts = memory.env["ir.module.module"].search([]).read(["name", "shortdesc", "summary", "description", "author", "maintainer"])
            if len(module_dicts) != 0: 
                raw_documents = [self.create_document(text=json.dumps(module_dict),metadata=module_dict) for module_dict in module_dicts]
                self.create_faiss(raw_documents)

    def action_test_rag(self):
        _logger.error("runs"*100)
        action = {
            'name': 'Test Action',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.memory.test.wizard',
            'view_mode': 'form',
            'context': {'default_ai_memory': self.id},
            'target': 'new'
        }
        _logger.error(f"{action}")
        return action


    def load_faiss(self):
        if self.memory_faiss:
            faiss_file = base64.b64decode(self.memory_faiss)
            db = FAISS.deserialize_from_bytes(faiss_file,eval(self.ai_agent_llm_id.get_embedding()), allow_dangerous_deserialization=True)
            return db
        else:
            return False

    def text_splitter(self,documents):
        return RecursiveCharacterTextSplitter(
        chunk_size=self.split_chunk_size,  # chunk size (characters)
        chunk_overlap=self.split_chunk_overlap,  # chunk overlap (characters)
        add_start_index=True,  # track index in original document
    ).split_documents(documents)

    def create_document(self,text,metadata):
        return Document(id=uuid.uuid4(), page_content=text, metadata=metadata)

    def create_document_from_file(self, attachment_id):
        content = base64.b64decode(attachment_id.datas).decode("utf-8")
        if attachment_id.name.split(".")[-1] == "pdf":
            reader = PyPDF2.PdfFileReader(file)
            content = "\n".join(map(lambda page: page.extract_text(), reader.pages))
        _logger.error(f"{content}")     
        return Document(id=uuid.uuid4(), page_content=f"{content}", metadata={"name": attachment_id.name, "type": "attachment"})
        
    def create_faiss(self,raw_documents):
        documents = self.text_splitter(raw_documents)
        db = FAISS.from_documents(documents, eval(self.ai_agent_llm_id.get_embedding()))
        self.memory_faiss = base64.b64encode(db.serialize_to_bytes())


    def chat(self, query):
        db = self.load_faiss_index()
        model_name = "AI-Sweden-Models/gpt-sw3-6.7b-v2"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name)
        pipe = pipeline("text-generation", model=model, tokenizer=tokenizer, max_new_tokens=100)
        llm = HuggingFacePipeline(pipeline=pipe)

        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        qa = ConversationalRetrievalChain.from_llm(llm, db.as_retriever(), memory=memory)

        #self.qa_chain({"question": query, "chat_history": chat_history})
        result = qa({"question": query})
        return result['answer']

    def log_message(self, body, is_error=False):
        if is_error:
            self.status = "error"
        self.message_post(body=f"{body} | {self.last_run}", message_type="notification")

    def run(self):
        if self.status == "active":
            self.last_run = fields.Datetime.now()
            if self.memory_type == 'bs4' and self.url:
                all_pages = self.scrape_website(self.url)
                _logger.warning(f"scrape ended {len(all_pages)=} -----------------------------------------")
                self.memory_markdown = base64.b64encode(self.scrape_website(self.url))
            elif self.memory_type == 'model':
                self.rag_models()
            elif self.memory_type == "module":
                self.rag_modules()
            elif 'attachment' in self.memory_type:
                self.rag_attatchemts()
        else:
            raise UserError(_(f"Wrong state on memory ({self.name})"))          
            
    def scrape_website(self,website):
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
            visited.add(url)
            
            try:
                response = requests.get(url)
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Save page content as attachment
                content = soup.get_text()
                all_pages += markdownify.markdownify(content)
                
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
