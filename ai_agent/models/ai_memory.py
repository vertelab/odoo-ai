import json
import logging
import os
import io
from httpx import HTTPStatusError
from langchain.chains import ConversationalRetrievalChain
from langchain.chains import RetrievalQA
from langchain.document_loaders import TextLoader
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.llms import HuggingFacePipeline
from langchain.memory import ConversationBufferMemory
from langchain.schema import SystemMessage, HumanMessage
# ~ from langchain_community.text_splitters import CharacterTextSplitter
from langchain.vectorstores import FAISS
from langchain_anthropic import ChatAnthropic
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langchain_mistralai import ChatMistralAI
from langchain_openai import ChatOpenAI
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
from random import randint
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline

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


class AIMemory(models.Model):
    _name = 'ai.memory'
    _inherit = ["mail.thread", "mail.activity.mixin", ]

    _description = 'AI Memory'

    ai_type = fields.Selection(selection=[("default", "Default"), ('ai-programmer', 'AI Programmer')],
                               default="default", required=True)
    color = fields.Integer(default=lambda self: randint(1, 11))
    debug = fields.Boolean(string='Debug')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    name = fields.Char(required=True)
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    image_128 = fields.Image("Image", max_width=128, max_height=128)
    base_image_128 = fields.Image("Base Image", max_width=128, max_height=128, compute='_compute_base_image_128')
    memory_type = fields.Selection(selection=[('faiss', 'FAISS'), ('st', 'Short Term')], string='Memory type')
    memory_faiss = fields.Binary(string='FAISS Index', attachment=True)
    nbr_days = fields.Integer(string='Number days this memory will live')

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

    def run(self):
        self.last_run = fields.Datetime.now()

    def cron(self):
        self.env['ai.memory'].search(
            [('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
