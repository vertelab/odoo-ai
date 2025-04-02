import logging
import json

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError, ValidationError
from odoo.addons.ai_agent_pgvector.fields.fields import PgVector
from odoo.addons.ai_agent_pgvector.models.embedding_mixin import EmbeddingMixin

_logger = logging.getLogger(__name__)

class AIAgentMemoryRAG(models.Model):
    _name = 'ai.memory.rag'
    _description = 'AI Agent Memory RAG'
    _inherit = "llm.embedding.mixin"

    ai_memory_id = fields.Many2one(comodel_name="ai.memory")
    original_text = fields.Text()
    embedding = PgVector(dimension=768)
    metadata = fields.Text()

  
        
        
        
        
        
        
        
        
        
        
        
        

        
