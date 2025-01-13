import json
import logging
import os
import io
from langchain.chains import ConversationalRetrievalChain
from langchain.document_loaders import TextLoader
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.llms import HuggingFacePipeline
from langchain.memory import ConversationBufferMemory
from langchain.schema import SystemMessage, HumanMessage
# ~ from langchain_community.text_splitters import CharacterTextSplitter
# ~ from langchain.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
from random import randint
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import asyncio
from crawl4ai import AsyncWebCrawler

from dateutil.relativedelta import relativedelta

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


class AIMemory(models.Model):
    _name = 'ai.memory'
    _inherit = ["mail.thread", "mail.activity.mixin", ]

    _description = 'AI Memory'

    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],default="default", required=True)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    llm_id = fields.Many2one(comodel_name="ai.agent.llm", string="LLM", help="Choose Embedded Large Language Model",domain="[('status','=','confirmed')]")
    memory_faiss = fields.Binary(string='FAISS Index', attachment=True)
    memory_markdown = fields.Binary(string='Markdown', attachment=True)
    memory_type = fields.Selection(selection=[('faiss', 'FAISS'), ('st', 'Short Term')], string='Memory type')
    name = fields.Char(required=True)
    nbr_days = fields.Integer(string='Number days this memory will live')
    status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    url = fields.Char(string='Url', trim=True, )

    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128

    def save_faiss_index(self, index):
        buffer = io.BytesIO()
        faiss.write_index(index, faiss.swig_ptr(buffer))
        self.memory_faiss = buffer.getvalue()

    def load_faiss_index(self):
        if not self.memory_faiss:
            return None
        buffer = io.BytesIO(self.memory_faiss)
        return faiss.read_index(faiss.swig_ptr(buffer))

    def add_document(self, attachment_id):
        attachment = self.env['ir.attachment'].browse(attachment_id)
        if not attachment.exists():
            raise ValueError("Attachment not found")

        file_content = BytesIO(base64.b64decode(attachment.datas))
        loader = TextLoader(file_content, encoding='utf-8')
        documents = loader.load()
        # ~ text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        # ~ docs = text_splitter.split_documents(documents)
        docs = None
        db = self.load_faiss_index() or self.create_faiss_index()
        db.add_documents(docs)
        self.save_faiss_index(db.index)

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


# sudo apt install libgstreamer-gl1.0-0 libgstreamer-plugins-base1.0-0 libflite1 libavif-dev libharfbuzz-icu0 libenchant-2-2 libsecret-1-0 libhyphen0 libmanette-0.2-0 libgles2
# npx playwright install --with-deps
#playwright install
# npx playwright install-deps chromium
#aw@odoo16:~/.cache$ sudo cp -r ms-playwright /var/lib/odoo/.cache/
    def run(self):
        self.last_run = fields.Datetime.now()
        if self.url:                
            async def run_crawler():
            # Create an instance of AsyncWebCrawler
                async with AsyncWebCrawler() as crawler:
                    # Run the crawler on a URL
                    result = await crawler.arun(url=self.url)
                    return result
            # Use asyncio.run() to execute the async function in a synchronous manner
            self.memory_markdown = asyncio.run(run_crawler())        
        

    def cron(self):
        self.env['ai.memory'].search(
            [('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
