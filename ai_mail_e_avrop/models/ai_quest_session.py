from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSession(models.Model):
    _inherit = 'ai.quest.session'

    ai_type = fields.Selection(selection_add=[('e-avrop', 'E-avrop')], ondelete={'e-avrop': 'cascade'})

    def _message_set_main_attachment_id(self, attachment_ids):
        thread_ids = super(AIQuestSession,self)._message_set_main_attachment_id(attachment_ids)

        _logger.error(f"{self.session=}")

        if self.ai_type == "e-avrop":
            self.ai_quest_id.mail(mail=self.message_ids[0],session=self)

        return thread_ids

    # @api.model
    def message_process(self, model, message, custom_values=None,
                        save_original=False, strip_attachments=False,
                        thread_id=None):
        thread_id = super(AIQuestSession,self).message_process(
                                                model, 
                                                message, 
                                                custom_values=custom_values,
                                                save_original=save_original or self.ai_type == "e-avrop",
                                                strip_attachments=strip_attachments,
                                                thread_id=thread_id
                                            )
        return thread_id