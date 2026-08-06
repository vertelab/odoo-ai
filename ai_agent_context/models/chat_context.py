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

    Captures the user's current view context (active_model, active_id,
    view_type) from multiple sources and stores it on the channel.
    The quest's _detect_record() and _extra_context() then read it
    during quest execution.

    Context capture happens at multiple points:
    1. message_post() — auto-capture from HTTP request / env.context
    2. set_channel_context() — explicit capture from a record
    3. Frontend controller — explicit push from JS via RPC
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

    # ── message_post() auto-capture ──────────────────────────────────

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        """Auto-capture context when a message is posted to this channel.

        This runs BEFORE super().message_post() so the context is available
        to any quest that reads the channel during message processing.
        """
        self._capture_ai_context()
        return super().message_post(**kwargs)

    def _capture_ai_context(self):
        """Auto-detect and store the user's current view context.

        Tries multiple sources in priority order:
        1. env.context from with_context() — set by quest.run() and
           frontend RPC calls (most reliable)
        2. HTTP request params — set by Odoo action system
        3. Channel's own model/res_id — fallback for channels linked
           to specific records
        4. Already-stored context — don't overwrite if we have it
        """
        # Already have context? Only overwrite if we have BETTER data
        existing_model = self.ai_context_model
        existing_id = self.ai_context_record_id

        sources = self._resolve_context_sources()

        # Priority: use the BEST available source
        model = sources['env_model'] or sources['http_model'] or sources['channel_model']
        record_id = sources['env_id'] or sources['http_id'] or sources['channel_id']
        view_type = sources['env_view'] or sources['http_view'] or ''

        if not model:
            _logger.debug("CTX-INJECT [_capture] no context found from any source")
            return

        # Don't overwrite existing context with worse data
        # (e.g., don't replace specific record with generic model-only context)
        if existing_model and existing_id:
            if not record_id:
                _logger.debug(
                    "CTX-INJECT [_capture] keeping existing context "
                    "(%s#%s) over model-only (%s)",
                    existing_model, existing_id, model
                )
                return

        update_vals = {}
        if model and model != existing_model:
            update_vals['ai_context_model'] = model
        if record_id and record_id != existing_id:
            update_vals['ai_context_record_id'] = int(record_id)
        if view_type and view_type != self.ai_context_view_type:
            update_vals['ai_context_view_type'] = view_type

        if update_vals:
            _logger.info(
                "CTX-INJECT [_capture] SET: model=%s record_id=%s view=%s "
                "(sources: env=%s/%s http=%s/%s)",
                model, record_id, view_type,
                sources['env_model'], sources['env_id'],
                sources['http_model'], sources['http_id'],
            )
            self.write(update_vals)

    def _resolve_context_sources(self):
        """Resolve ALL possible context sources.

        Returns a dict with keys: env_model, env_id, env_view,
        http_model, http_id, http_view, channel_model, channel_id.

        This is separated from _capture_ai_context() so it can be
        tested independently.
        """
        result = {
            'env_model': None, 'env_id': None, 'env_view': None,
            'http_model': None, 'http_id': None, 'http_view': None,
            'channel_model': None, 'channel_id': None,
        }

        # Source 1: ORM env.context (MOST RELIABLE — set by with_context())
        env_ctx = self.env.context
        result['env_model'] = (
            env_ctx.get('context_record_model')
            or env_ctx.get('_ai_context_model')
            or env_ctx.get('active_model')
        )
        result['env_id'] = (
            env_ctx.get('context_record_id')
            or env_ctx.get('_ai_context_id')
            or env_ctx.get('active_id')
        )
        result['env_view'] = env_ctx.get('view_type')

        # Source 2: HTTP request (set by Odoo action system)
        req = http_request
        if req:
            ctx = getattr(req, 'context', {}) or {}
            params = getattr(req, 'params', {}) or {}

            result['http_model'] = (
                ctx.get('active_model')
                or params.get('model')
                or params.get('active_model')
            )
            result['http_id'] = (
                ctx.get('active_id')
                or params.get('id')
                or params.get('active_id')
            )
            result['http_view'] = ctx.get('view_type')

        # Source 3: Channel's own model/res_id (generic reference)
        result['channel_model'] = (
            getattr(self, 'model', None)
            or getattr(self, 'res_model', None)
        )
        result['channel_id'] = getattr(self, 'res_id', None)

        # Convert IDs to int if they're strings
        for key in ('env_id', 'http_id', 'channel_id'):
            if result[key] is not None:
                try:
                    result[key] = int(result[key])
                except (ValueError, TypeError):
                    pass

        return result

    # ── Explicit context setting ─────────────────────────────────────

    def set_channel_context(self, record=None, model=None, record_id=None,
                            view_type=None):
        """Explicitly set AI context on this channel.

        This is the PREFERRED way to set context — call it with an
        actual record object or model+ID pair. It's called by:
        - Frontend controller when launching quest from form view
        - Quest execution when a record is detected
        - Other modules that need to inject context

        :param record: Odoo record (BaseModel) — preferred
        :param model: str — technical model name
        :param record_id: int — record ID
        :param view_type: str — view type (form, list, kanban, etc.)
        :return: self (for chaining)
        """
        self.ensure_one()

        if record is not None and hasattr(record, 'exists') and record.exists():
            model = record._name
            record_id = record.id
            # Also store the serialized record data in the session if available
            self._store_record_snapshot(record)

        if not model:
            _logger.warning("CTX-INJECT [set_channel_context] called without model")
            return self

        vals = {
            'ai_context_model': model,
        }
        if record_id:
            vals['ai_context_record_id'] = record_id
        if view_type:
            vals['ai_context_view_type'] = view_type

        _logger.info(
            "CTX-INJECT [set_channel_context] channel=%s model=%s record_id=%s "
            "view=%s",
            self.uuid[:8] if hasattr(self, 'uuid') else self.id,
            model, record_id, view_type,
        )

        self.write(vals)
        return self

    def _store_record_snapshot(self, record):
        """Store a serialized field snapshot on the quest session.

        If this channel is linked to an active quest session, store the
        record's fields there for use by _extra_context().
        """
        if not hasattr(record, '_ai_serialize_fields_data'):
            return

        session = getattr(self, 'ai_quest_session_id', None)
        if not session or session.status != 'active':
            return

        try:
            session.context_json = record._ai_serialize_fields_data()
            session.context_record_model = record._name
            session.context_record_id = record.id
        except Exception as e:
            _logger.debug("Failed to store record snapshot on session: %s", e)

    # ── send_ai_message() integration ─────────────────────────────────

    def send_ai_message(self, message):
        """Log context values and ensure they're available to the quest.

        This is called by ai_agent when processing messages. We ensure
        context is captured before the quest reads it.
        """
        self._capture_ai_context()

        _logger.info(
            "CTX-INJECT [send_ai] channel=%s model=%s record_id=%s view=%s",
            getattr(self, 'uuid', self.id), self.ai_context_model,
            self.ai_context_record_id, self.ai_context_view_type
        )
        return super().send_ai_message(message)

    # ── Helper: resolve to actual record ──────────────────────────────

    def get_context_record(self):
        """Resolve the stored context to an actual Odoo record.

        :return: Record or None if no valid context
        :rtype: BaseModel or None
        """
        self.ensure_one()
        if not self.ai_context_model or not self.ai_context_record_id:
            return None

        try:
            record = self.env[self.ai_context_model].browse(
                self.ai_context_record_id
            )
            return record if record.exists() else None
        except Exception as e:
            _logger.debug("Failed to resolve context record: %s", e)
            return None
