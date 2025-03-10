from odoo import api, fields, models, _
from datetime import date as date_type
from datetime import datetime, timedelta
from odoo.tools import date_utils
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

class FieldServiceOrderLineEmployee(models.Model):
    _inherit = 'fieldservice.order.line.employee'
    
    def set_member_of_chat(self):
        for line_employee in self:
            channel_id = False
            if line_employee.fieldservice_order_line_id and line_employee.fieldservice_order_line_id.order_id and line_employee.fieldservice_order_line_id.order_id.ai_quest_id:
               quest_id = line_employee.fieldservice_order_line_id.order_id.ai_quest_id
            if quest_id:
               channel_id = quest_id.channel_id
            if line_employee.employee_id and line_employee.employee_id.user_id and channel_id:
               partner_id = line_employee.employee_id.user_id.partner_id
               already_partner = False
               for member in channel_id.channel_member_ids:
                   if partner_id == member.partner_id:
                      already_partner = True
                      break
               if not already_partner:
                  line_employee.env['discuss.channel.member'].create({'channel_id':channel_id.id,'partner_id':partner_id.id})

    @api.model
    def create(self, vals):
        line_employee = super(FieldServiceOrderLineEmployee, self).create(vals)
        if line_employee.fieldservice_order_line_id and line_employee.fieldservice_order_line_id.order_id and line_employee.fieldservice_order_line_id.order_id.ai_quest_id:
           quest_id = line_employee.fieldservice_order_line_id.order_id.ai_quest_id
           if quest_id.channel_id:
              line_employee.set_member_of_chat(quest_id.channel_id)
        return line_employee


    def write(self, vals):
        line_employee = super(FieldServiceOrderLineEmployee, self).write(vals)
        if employee_id in vals:
           self.set_member_of_chat()
