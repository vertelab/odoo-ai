# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import logging

from odoo import models, fields, api
from odoo.http import request as http_request

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    """Inject request-level context into AI chat quests.
    
    Overrides send_ai_message() to capture the user's current view context
    (active_model, active_id, view_type) from the HTTP request and store it
    on the channel. The quest's _extra_context() then reads it during graph
    building.
    """
    _inherit = 'discuss.channel'

    ai_context_model = fields.Char(
        string='AI Context Model',
        help="The model the user was viewing when the AI chat was triggered."
    )
    ai_context_record_id = fields.Integer(
        string='AI Context Record ID',
        help="The record ID the user was viewing."
    )
    ai_context_view_type = fields.Char(
        string='AI Context View Type',
        help="The view type (form, list, kanban, etc.)."
    )

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        self._capture_ai_context()
        return super().message_post(**kwargs)

    def _capture_ai_context(self):
        """Read the user's current view context from the HTTP request."""
        req = http_request
        _logger.info("CTX-INJECT [_capture] http_request=%s", bool(req))
        
        if not req:
            _logger.info("CTX-INJECT [_capture] no HTTP request available")
            return

        # Try multiple sources for context
        ctx = getattr(req, 'context', {}) or {}
        params = getattr(req, 'params', {}) or {}
        env_ctx = self.env.context
        
        _logger.info("CTX-INJECT [_capture] req.context keys=%s",
                     list(ctx.keys())[:10] if ctx else 'EMPTY')
        _logger.info("CTX-INJECT [_capture] env.context keys=%s",
                     list(env_ctx.keys())[:10])

        # Try multiple sources for model/record (priority order)
        model = None
        record_id = None
        
        # 1. HTTP request params (from action)
        model = ctx.get('active_model') or params.get('model') or params.get('active_model')
        record_id = ctx.get('active_id') or params.get('id') or params.get('active_id')
        
        # 2. ORM environment context
        if not model:
            model = env_ctx.get('active_model')
            record_id = env_ctx.get('active_id')
        
        # 3. Check if this is a channel attached to a record
        if not model:
            model = getattr(self, 'model', None) or getattr(self, 'res_model', None)
            record_id = getattr(self, 'res_id', None)

        view_type = ctx.get('view_type') or env_ctx.get('view_type') or 'unknown'

        _logger.info("CTX-INJECT [_capture] resolved: model=%s record_id=%s view=%s",
                     model, record_id, view_type)

        if model:
            self.ai_context_model = model
        if record_id:
            self.ai_context_record_id = int(record_id)
        if view_type:
            self.ai_context_view_type = view_type
