# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AIAgentContextController(http.Controller):
    """Controller endpoints for ai_agent_context injection.

    Provides JSONRPC endpoints that the frontend can call to push
    the user's current view context (model, record ID, view type)
    to the backend before a quest is launched.
    """

    # ── Push context from frontend ───────────────────────────────────

    @http.route(
        '/ai_agent_context/set_context',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def set_context(self, channel_id, model=None, res_id=None,
                    view_type=None, record_data=None):
        """Push current view context from frontend to backend.

        Called by the Quest systray button and form controller patches.
        Stores the context on the discuss channel so the quest's
        _detect_record() and _extra_context() can read it.

        :param channel_id: int — discuss.channel ID
        :param model: str — technical model name (e.g., 'sale.order')
        :param res_id: int — record ID the user is viewing
        :param view_type: str — 'form', 'list', 'kanban', etc.
        :param record_data: dict — serialized record fields (optional)
        :return: dict with success status
        """
        _logger.info(
            "CTX-INJECT [RPC set_context] channel=%s model=%s res_id=%s view=%s",
            channel_id, model, res_id, view_type,
        )

        channel = request.env['discuss.channel'].browse(int(channel_id))
        if not channel.exists():
            return {'success': False, 'error': 'Channel not found'}

        # Set context on the channel
        channel.set_channel_context(
            model=model,
            record_id=res_id,
            view_type=view_type,
        )

        # If we have record data, store it on the linked session
        if record_data and model and res_id:
            self._store_record_snapshot(
                channel, model, res_id, record_data
            )

        return {'success': True}

    def _store_record_snapshot(self, channel, model, res_id, record_data):
        """Store serialized record data on the active quest session.

        :param channel: discuss.channel record
        :param model: str — technical model name
        :param res_id: int — record ID
        :param record_data: dict — serialized field values
        """
        try:
            record = request.env[model].browse(int(res_id))
            if not record.exists():
                return

            # Find active sessions (they track their own context_record_*)
            session = request.env['ai.quest.session'].search([
                ('status', '=', 'active'),
                ('context_record_id', '=', False),
            ], limit=1, order='create_date DESC')

            if session:
                session.write({
                    'context_record_model': model,
                    'context_record_id': res_id,
                    'context_json': json.dumps(
                        record_data, default=str, ensure_ascii=False, indent=2
                    ) if isinstance(record_data, dict) else record_data,
                })
                _logger.info(
                    "CTX-INJECT [RPC] stored snapshot on session %s", session.id
                )
        except Exception as e:
            _logger.debug("Failed to store record snapshot: %s", e)

    # ── Launch quest with context ─────────────────────────────────────

    @http.route(
        '/ai_agent_context/launch_quest',
        type='json',
        auth='user',
        methods=['POST'],
    )
    def launch_quest(self, quest_id, model=None, res_id=None,
                     view_type=None, record_data=None):
        """Launch a quest with record context.

        Creates a session and a discuss channel, pushes context,
        then runs the quest — all in one RPC call.

        :param quest_id: int — ai.quest ID
        :param model: str — technical model name
        :param res_id: int — record ID
        :param view_type: str — view type
        :param record_data: dict — serialized record fields
        :return: dict with channel_id and session_id
        """
        _logger.info(
            "CTX-INJECT [RPC launch_quest] quest=%s model=%s res_id=%s",
            quest_id, model, res_id,
        )

        quest = request.env['ai.quest'].browse(int(quest_id))
        if not quest.exists():
            return {'success': False, 'error': 'Quest not found'}

        # Create the discuss channel for this quest
        channel = request.env['discuss.channel'].create({
            'name': f"Quest: {quest.name}",
            'channel_member_ids': [
                (0, 0, {'partner_id': request.env.user.partner_id.id}),
            ],
        })

        # Set context on channel
        channel.set_channel_context(
            model=model,
            record_id=res_id,
            view_type=view_type,
        )

        # Create session
        session = request.env['ai.quest.session'].quest_init(
            quest,
            model=model,
            record_id=res_id,
        )

        # Run the quest with the record context
        if model and res_id:
            try:
                record = request.env[model].browse(int(res_id))
                if record.exists():
                    quest.with_context(
                        context_record_model=model,
                        context_record_id=res_id,
                    ).run(
                        session=session,
                        records=record,
                        channel=channel,
                    )
            except Exception as e:
                _logger.error("Quest run failed: %s", e, exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'channel_id': channel.id,
                    'session_id': session.id,
                }
        else:
            quest.run(session=session, channel=channel)

        return {
            'success': True,
            'channel_id': channel.id,
            'session_id': session.id,
        }
