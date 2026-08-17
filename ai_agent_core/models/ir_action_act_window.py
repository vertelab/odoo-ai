# -*- coding: utf-8 -*-
"""Registrera org_chart som giltig view_mode på act_window.view."""

from odoo import fields, models


class IrActionsActWindowView(models.Model):
    _inherit = 'ir.actions.act_window.view'

    view_mode = fields.Selection(selection_add=[('org_chart', 'Org Chart')], ondelete={'org_chart': 'cascade'})
