from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)

class AIMemoryTestWizard(models.Model):
    _name = 'ai.memory.test.wizard'
    _description = 'A testing wizard for ai memory'

    is_raise_error = fields.Boolean(default=True)
    test_rag_input = fields.Char()
    ai_memory = fields.Many2one(comodel_name="ai.memory")
    ai_vector_type = fields.Selection(related="ai_memory.vector_type")

    def test_rag(self):
        if self.ai_vector_type == "faiss":
            db = self.ai_memory.load_faiss()
            if not db:
                if self.is_raise_error:
                    raise UserError(_("Failed to load database"))
                _logger.error("Failed to load database")
                return
            docs = db.similarity_search(self.test_rag_input)
            if not docs:
                if self.is_raise_error:
                    raise UserError(_("No serach results found"))
                _logger.error("No serach results found")
            whole_doc = "\n".join([doc.page_content for doc in docs])
            if self.is_raise_error:
                raise UserError(f"{whole_doc}")
            _logger.info(f"{whole_doc}")