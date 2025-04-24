import logging
import math
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

_logger = logging.getLogger(__name__)

class AIMemory(models.Model):
    _inherit = 'ai.memory'

    vector_type = fields.Selection(selection_add=[("pg_vector", "Postgres Vector")],ondelete={'pg_vector': 'cascade'})
    ai_memory_rag_ids = fields.One2many(comodel_name="ai.memory.rag",inverse_name="ai_memory_id") 

    def create_vector(self,documents,memory):
        split_documents, embeddings = super(AIMemory,self).create_vector(documents,memory)
        if self.vector_type == "pg_vector":
            self.setup_pg_vector()
            if memory.document_chunks != 0:
                if len(split_documents) < memory.document_chunks:
                    raise UserError(f"The chunks per session ({memory.document_chunks}) is less than the number of documents after they have been split ({len(split_documents)}), which is not allowed. If you are using a model, a tip is to not set the chunks per session to be bigger than the amount of records you have.")
                runs = math.ceil(len(split_documents) / memory.document_chunks)
                for run in range(runs):
                    docs_to_embedd = split_documents[run * memory.document_chunks:(run + 1) * memory.document_chunks]
                    self.store_in_pg_vector(docs_to_embedd,embeddings)
            else:
                self.store_in_pg_vector(split_documents,embeddings)
            
            # if memory.memory_type == "model" and memory.model_id:
            #     self.pg_vector_create_column(documents, embeddings, memory)
            # else:
            #     self.pg_vector_create_table(documents, embeddings)
        return documents, embeddings

    def create_text_embeddings(self,documents,embeddings):
        text_embeddings = [embeddings.embed_query(document.page_content) for document in documents]
        return text_embeddings

    def store_in_pg_vector(self,documents,embeddings):
        text_embeddings = self.create_text_embeddings(documents,embeddings)
        for embedding,document in zip(text_embeddings,documents):
            self.env["ai.memory.rag"].sudo().create({"ai_memory_id":self.id, "original_text":document.page_content, "embedding":embedding ,"metadata":document.metadata})
        return text_embeddings

    def setup_pg_vector(self):
        sql_check_for_pg_vector_extension = "SELECT * FROM pg_available_extensions where name='vector';"
        self.env.cr.execute(sql_check_for_pg_vector_extension)
        sql_response = self.env.cr.fetchall()
        _logger.error(f"{sql_response=}")
        if sql_response:
            try:
                sql_add_pg_vector_extension = "CREATE EXTENSION IF NOT EXISTS vector;"
                self.env.cr.execute(sql_add_pg_vector_extension)
                self.env.cr.commit()
            except:
                raise UserError("Could not add pg_vector extension. Might need to set Odoo as a superuser in Postgres.")
        else:
            raise UserError("The Postgres extension vector is not installd")
    
    def check_if_column_exsists(self,db_model_name):
        sql_check_if_column_exsists = f"SELECT EXISTS(SELECT 'vector' FROM information_schema.columns WHERE table_name='{db_model_name}' and column_name='embedding');"
        self.env.cr.execute(sql_check_if_column_exsists)
        sql_response = self.env.cr.fetchall()
        return sql_response[0][0]
    
    def pg_vector_get_column(self,ids=[]):
        db_model_name = memory.model_name.replace(".","_")
        if self.check_if_column_exsists(db_model_name) == False:
            raise UserError("Ther is no embedding column on this model")
        sql_select_embedding = f"SELECT embedding FROM {db_model_name};" 
        self.env.cr.execute(sql_select_embedding)
        sql_response = self.env.cr.fetchall()
        return sql_response
    
    def pg_vector_create_column(self,documents,embeddings,memory):
        db_model_name = memory.model_name.replace(".","_")
        text_embeddings = self.create_text_embeddings(documents,embeddings)
        
        if self.check_if_column_exsists(db_model_name) == False:
            sql_add_column = f"ALTER TABLE {db_model_name} ADD embedding vector(768);"
            self.env.cr.execute(sql_add_column)
            self.env.cr.commit()
        
        for text_embedding, document in zip(text_embeddings,documents):
            _logger.error(f"{text_embedding=}")
            sql_insert_values = f"UPDATE {db_model_name} SET embedding = '{text_embedding}' WHERE id = {document.metadata.get('id')};"
            self.env.cr.execute(sql_insert_values)
        self.env.cr.commit()
    
    def pg_vector_create_table(self,documents,embeddings):
        text_embeddings = self.create_text_embeddings(documents,embeddings)
        
        sql_check_if_table_exsists = "SELECT EXISTS (SELECT FROM pg_tables WHERE  schemaname = 'public' AND tablename  = 'documents');"
        self.sudo().env.cr.execute(sql_check_if_table_exsists)
        sql_response = self.env.cr.fetchall()
        _logger.error(f"{sql_response[0][0]=}")
        
        if sql_response == False:
    
            sql_create_table = f"""CREATE TABLE documents (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL,
                        embedding vector    (768),  -- Match your embedding model's dimensions
                        metadata JSONB           -- Optional metadata (e.g., source, author)
                        );"""
            sql_setup_index_for_table = "CREATE INDEX ON documents USING hnsw (embedding vector_l2_ops);"
            self.env.cr.execute(sql_create_table)
            self.env.cr.execute(sql_setup_index_for_table)
            self.env.cr.commit()
        
        for text_embedding, document in zip(text_embeddings, documents):
            jsonb = json.dumps(document.metadata)
            _logger.error(f"{type(document.metadata)=}")
            sql_insert_values = f"INSERT INTO documents (content, embedding, metadata) VALUES ('{document.page_content}', '{text_embedding}', '{jsonb}');"
            self.env.cr.execute(sql_insert_values)
        self.env.cr.commit()
            