from odoo import api, fields, models, _
from datetime import date as date_type
from datetime import datetime, timedelta
import logging
_logger = logging.getLogger(__name__)

class FieldServiceOrder(models.Model):
    _inherit = 'fieldservice.order'

    ai_quest_id = fields.Many2one(comodel_name='ai.quest',string="",help="")

    @api.depends("stage_id")
    def _onchange_stage_id(self):
        _logger.warning("_onchange_stage_id"*100)
        if self.stage_id.start_quest:
           self.start_quest()
        elif not self.stage_id.is_closed:
            if self.ai_quest_id:
                self.ai_quest_id.status = 'active'
                self.ai_quest_id.channel_id.write({'active': True,})
        else:
            if self.ai_quest_id.channel_id:
                self.ai_quest_id.status = 'done'
                self.ai_quest_id.channel_id.write({'active': False})

    @api.depends("name")
    def _onchange_name(self):
        if self.ai_quest_id:
            self.ai_quest_id.write({'name': f"{self.name}"})
            if self.ai_quest_id.channel_id:
                self.ai_quest_id.channel_id.write({'name': f"{self.name}"})


    @api.model
    def create(self, vals):
        field_service_order_id = super(FieldServiceOrder, self).create(vals)
        if field_service_order_id.stage_id.start_quest:
           field_service_order_id.start_quest()
        return field_service_order_id

    def start_quest(self):
        for field_service_order_id in self:
          if not field_service_order_id.ai_quest_id:
            field_service_order_id.ai_quest_id = self.env['ai.quest'].create({
                'name': f"{field_service_order_id.name}",
                'ai_type': 'fieldservice-order',
                'init_type': 'channel',
                'status': 'active',
                'description':'A quest to help a service tech in the field.',
                'code': """result = quest.build(session=session,message=message_body).invoke(message_invoke)
                )"""
            })
            self.env['ai.quest.agent'].create({'ai_agent_id': self.env.ref('ai_fieldservice_vrtl.ai_agent_helpdesk_chat').id,'ai_quest_id': field_service_order_id.ai_quest_id.id})
            field_service_order_id.ai_quest_id.channel_id = self.env['discuss.channel'].create({
                'name': f"[{field_service_order_id.name}",
                'ai_quest_id': field_service_order_id.ai_quest_id.id,
                'description': _('Chat with fieldserviceorders'),
            })
            self.set_member_of_quest_chat()

    def set_member_of_quest_chat(self):
        for record in self:
            if record.ai_quest_id and record.ai_quest_id.channel_id:
               members = record.order_line_ids.fieldservice_order_line_employee_ids
               members.set_member_of_quest_chat()

    def write(self,vals):
          res = super(FieldServiceOrder, self).write(vals)
          if "name" in vals:
             self._onchange_name()
          if "stage_id" in vals:
             self._onchange_stage_id()
          return res

    
