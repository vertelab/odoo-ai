import json
import logging
import io
import PyPDF2
import base64
import uuid
import faiss
import asyncio
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


class AIMemory(models.Model):
    _name = 'ai.memory'
    _inherit = ["mail.thread", "mail.activity.mixin", ]

    _description = 'AI Memory'

    ai_type = fields.Selection(selection=[("default", "Default"),],default="default", required=True)
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
    split_chunk_size = fields.Integer(default=1000)
    split_chunk_overlap = fields.Integer(default=200)
    ai_agent_llm_id = fields.Many2one(string="Embedded LLM", comodel_name="ai.agent.llm", required=True, domain="[('status','=','confirmed')]")
    status = fields.Selection(selection=[("draft", _("Draft")), ("active", _("Active")), ("done", _("Done")), ("error", _("Error"))],default="draft")
    tag_ids = fields.Many2many(comodel_name='product.tag', string='Tags')
    url = fields.Char(string='Url', trim=True, )
    model_id = fields.Many2one(comodel_name='ir.model', )


    @api.depends('image_128')
    def _compute_base_image_128(self):
        for record in self:
            record.base_image_128 = record.image_128

    def rag_attatchemts(self):
        for memory in self:
            raw_documents = [self.create_document_from_file(attachment) for attachment in 
                         self.env["ir.attachment"].search([("res_model", "=", memory._name), ("res_id", "=", memory.id)])]
            if raw_documents:
                documents = self.text_splitter(raw_documents)
                self.create_faiss(documents)
            else:
                raise UserError(_("No attachments to RAG"))
        
    def rag_models(self):
        for memory in self:
            model_ids = memory.env["ir.model"].search([])
            raw_documents = [self.create_document(model_id) for model_id in model_ids]
            documents = self.text_splitter(raw_documents)
            self.create_faiss(documents)

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
        text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=self.split_chunk_size,  # chunk size (characters)
        chunk_overlap=self.split_chunk_overlap,  # chunk overlap (characters)
        add_start_index=True,  # track index in original document
    )
        return text_splitter(documents)

    def create_document(self,text):
        pass

    def create_document_from_file(self, attachment_id):
        content = base64.b64decode(attachment_id.datas).decode("utf-8")
        if attachment_id.name.split(".")[-1] == "pdf":
            reader = PyPDF2.PdfFileReader(file)
            content = "\n".join(map(lambda page: page.extract_text(), reader.pages))
        _logger.error(f"{content}")     
        return Document(id=uuid.uuid4(), page_content=f"{content}", metadata={"name": attachment_id.name, "type": "attachment"})
        
    def create_faiss(self,documents):
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

    # def run(self):


# sudo apt install libgstreamer-gl1.0-0 libgstreamer-plugins-base1.0-0 libflite1 libavif-dev libharfbuzz-icu0 libenchant-2-2 libsecret-1-0 libhyphen0 libmanette-0.2-0 libgles2
# npx playwright install --with-deps
#playwright install
# npx playwright install-deps chromium
#aw@odoo16:~/.cache$ sudo cp -r ms-playwright /var/lib/odoo/.cache/
    # def run(self):
    #     self.last_run = fields.Datetime.now()
    #     if self.url:                
    #         async def run_crawler():
    #         # Create an instance of AsyncWebCrawler
    #             async with AsyncWebCrawler() as crawler:
    #                 # Run the crawler on a URL
    #                 result = await crawler.arun(url=self.url)
    #                 return result
    #         # Use asyncio.run() to execute the async function in a synchronous manner
    #         self.memory_markdown = asyncio.run(run_crawler())        
        

    def cron(self):
        self.env['ai.memory'].search(
            [('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()
