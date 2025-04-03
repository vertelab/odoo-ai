from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)

class AIMemoryTestWizard(models.Model):
    _inherit = 'ai.memory.test.wizard'

    def test_rag(self):
        super(AIMemoryTestWizard,self).test_rag()
        _logger.error(f"{self.ai_memory.ai_memory_rag_ids=}")
        _logger.error(f"{self.ai_memory.ai_memory_rag_ids=}"*100)
        if self.ai_vector_type == "pg_vector" and self.ai_memory.ai_memory_rag_ids:
            embedder = self.ai_memory.ai_agent_llm_id.get_embedding()
            input_vector = embedder.embed_query(self.test_rag_input)
            whole_doc = self.env["ai.memory.rag"].search_similar(input_vector)
            answer = "\n\n".join([record.original_text for record in whole_doc[0]])
            if self.is_raise_error:
                raise UserError(f"{answer=}")
            _logger.info(f"{answer=}")