import logging
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

from pgvector.sqlalchemy import Vector


from langchain_postgres import PGVector
from langchain_postgres.vectorstores import PGVector
#https://python.langchain.com/api_reference/postgres/vectorstores/langchain_postgres.vectorstores.PGVector.html#langchain_postgres.vectorstores.PGVector


_logger = logging.getLogger(__name__)


class AIMemory(models.Model):
    _inherit = 'ai.memory'

    memory_faiss = fields.Binary(string='FAISS Index', attachment=True)
    vector_type = fields.Selection(selection_add=[("pg_vector", "Postgres Vector")],ondelete={'pg_vector': 'cascade'})
    
    def create_vector(self,raw_documents):
        super(AIMemory,self).create_vector(raw_documents)
        if self.vector_type == 'pg_vector':
            documents = self.text_splitter(raw_documents)
            db = FAISS.from_documents(documents, self.ai_agent_llm_id.get_embedding())
            self.memory_faiss = base64.b64encode(db.serialize_to_bytes())



class AIMemoryPgVector(models.Model):
    _name="ai.memory.pg_vector"
    
    memory_id = fields.Many2one(comodel_name="ai.memory")
    embedding = fields.Vector(string='Embedding', size=1000)
    # Add metadata
    # Add info about document, document.name page, images
    
    
    
    
    @api.model
    def find_similar(self, query_embedding, limit=5):
        self.env.cr.execute("""
            SELECT id, memorty_id,  
                   embedding <=> %s AS distance
            FROM ai_memory_pg_vector
            ORDER BY distance
            LIMIT %s
        """, (query_embedding, limit))
        return self.env.cr.dictfetchall()

    @api.model
    def create(self, vals):
        # Antag att vi har en funktion som genererar embedding
        embedding = self._generate_embedding(vals['name'], vals['description'])
        vals['embedding'] = embedding
        return super(ProductEmbedding, self).create(vals)

    def _generate_embedding(self, name, description):
        # Här skulle du implementera din logik för att generera embedding
        # Detta är bara en platshållare
        return [0.1] * 512
    
