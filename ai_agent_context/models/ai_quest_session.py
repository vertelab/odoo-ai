# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIQuestSession(models.Model):
    """Extend Quest Session with automatic record context injection.
    
    When a session is created with a record reference, the session
    automatically stores the record and pre-builds context for the AI.
    """
    _inherit = 'ai.coworker.session'

    context_record_model = fields.Char(
        string='Context Record Model',
        help="Technical name of the record model providing context."
    )
    context_record_id = fields.Integer(
        string='Context Record ID',
        help="ID of the record providing context."
    )
    context_record_name = fields.Char(
        string='Context Record Name',
        compute='_compute_context_record_name',
        help="Display name of the context record."
    )
    context_json = fields.Text(
        string='Context JSON',
        help="Pre-built JSON context for the AI agent, generated when "
             "the session is initialized with a record."
    )
    context_chatter = fields.Text(
        string='Context Chatter',
        help="Pre-built chatter history for the AI agent."
    )
    has_context = fields.Boolean(
        string='Has Context',
        compute='_compute_has_context',
        help="Whether this session has record context available."
    )

    @api.depends('context_record_model', 'context_record_id')
    def _compute_context_record_name(self):
        for session in self:
            if session.context_record_model and session.context_record_id:
                try:
                    record = self.env[session.context_record_model].browse(
                        session.context_record_id
                    )
                    session.context_record_name = (
                        record.display_name if record.exists() else False
                    )
                except Exception:
                    session.context_record_name = False
            else:
                session.context_record_name = False

    @api.depends('context_json', 'context_chatter')
    def _compute_has_context(self):
        for session in self:
            session.has_context = bool(
                session.context_json or session.context_chatter
            )

    def set_context_record(self, record):
        """Set the context record for this session and pre-build context.
        
        This method:
        1. Stores the record reference on the session
        2. Serializes record fields into context_json
        3. Serializes chatter history into context_chatter
        4. Creates a session_object link for traceability
        
        :param record: The Odoo record to use as context
        :type record: models.BaseModel
        """
        self.ensure_one()
        if not record or not record.exists():
            return False

        self.write({
            'context_record_model': record._name,
            'context_record_id': record.id,
        })

        # Create session object for traceability
        self.env['ai.session.object'].create({
            'ai_session_id': self.id,
            'object_id': f"{record._name},{record.id}",
        })

        # Build context JSON from record fields
        try:
            if hasattr(record, '_ai_serialize_fields_data'):
                self.context_json = record._ai_serialize_fields_data()
        except Exception as e:
            _logger.warning(
                "Failed to build context JSON for %s: %s", record, e
            )

        # Build chatter context
        try:
            if hasattr(record, '_ai_serialize_messages_data'):
                chatter = record._ai_serialize_messages_data()
                if chatter:
                    self.context_chatter = chatter
        except Exception as e:
            _logger.warning(
                "Failed to build chatter context for %s: %s", record, e
            )

        return True

    @api.model
    def quest_init(self, quest, record=None, **kwargs):
        """Extended quest_init that automatically sets context from a record."""
        # ai.coworker.session may not have quest_init; create session directly
        if hasattr(super(), 'quest_init'):
            session = super().quest_init(quest, **kwargs)
        else:
            session = self.create({
                'coworker_id': quest.id,
                'status': 'active',
                'name': quest.name or 'Session',
                **kwargs
            })

        if record and record.exists():
            session.set_context_record(record)

        return session

    def action_view_context_record(self):
        """Open the context record in a form view."""
        self.ensure_one()
        if not self.context_record_model or not self.context_record_id:
            return {'type': 'ir.actions.act_window_close'}

        return {
            'type': 'ir.actions.act_window',
            'res_model': self.context_record_model,
            'res_id': self.context_record_id,
            'view_mode': 'form',
            'target': 'current',
            'name': self.context_record_name or _('Context Record'),
        }
