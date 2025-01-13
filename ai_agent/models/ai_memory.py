import json
import logging
import os
import io
import PyPDF2
import base64
import uuid
import faiss
from langchain.chains import ConversationalRetrievalChain
from langchain_community.document_loaders import TextLoader
# from langchain.embeddings import HuggingFaceEmbeddings
# from langchain.llms import HuggingFacePipeline
from langchain.memory import ConversationBufferMemory
from langchain.schema import SystemMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
from random import randint
# from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents.base import Document
from langchain_text_splitters.character import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings



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
    split_chunk_size = fields.Integer(default=1000)
    split_chunk_overlap = fields.Integer(default=200)
    ai_agent_llm_id = fields.Many2one(string="Embedded LLM", comodel_name="ai.agent.llm", required=True, domain="[('status','=','confirmed')]")

    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128

    def rag_attatchemts(self):
        for ai_memory_id in self:
            ir_attachments_ids = self.env["ir.attachment"].search([("res_model", "=", ai_memory_id._name), ("res_id", "=", ai_memory_id.id)])
            if len(ir_attachments_ids) != 0:
                documents = []
                for ir_attachment_id in ir_attachments_ids:
                    documents.append(self.create_document(ir_attachment_id))
                all_splits = self.text_splitter().split_documents(documents)
                self.create_faiss(documents)
            else:
                raise UserError(_("No attachments to RAG"))
            
    def load_faiss(self):
        faiss_file = base64.b64decode(self.memory_faiss)
        db = FAISS.deserialize_from_bytes(faiss_file,eval(self.ai_agent_llm_id.get_embedding()), allow_dangerous_deserialization=True)
        return db

    def text_splitter(self):
        return RecursiveCharacterTextSplitter(
        chunk_size=self.split_chunk_size,  # chunk size (characters)
        chunk_overlap=self.split_chunk_overlap,  # chunk overlap (characters)
        add_start_index=True,  # track index in original document
    )

    def create_document(self, attachment_id):
        content = base64.b64decode(attachment_id.datas).decode("utf-8")
        if attachment_id.name.split(".")[-1] == "pdf":
            reader = PyPDF2.PdfFileReader(file)
            content = "\n".join(map(lambda page: page.extract_text(), reader.pages))
        _logger.error(f"{content}")     
        return Document(id=uuid.uuid4(), page_content=f"{content}", metadata={"name": attachment_id.name, "type": "attachment"})
        
    def create_faiss(self,documents):
        db = FAISS.from_documents(documents, eval(self.ai_agent_llm_id.get_embedding()))
        _logger.error(f"{db.serialize_to_bytes()=}")
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
        self.last_run = fields.Datetime.now()

    def cron(self):
        self.env['ai.memory'].search(
            [('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
