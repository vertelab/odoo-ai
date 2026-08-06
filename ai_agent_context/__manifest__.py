# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

{
    'name': 'odoo-ai: Context Injection for AI Quests',
    'version': '1.1',
    'summary': 'Injects record data and chatter history as context into AI Quest sessions',
    'category': 'AI Orchestration',
    'description': """
        AI Agent Context Injection
        ==========================
        
        Inspired by Odoo Enterprise AI, this module adds automatic context
        injection to AI Coworkers. When a Quest is triggered from a record
        form, the record's field values and chatter history are automatically
        serialized and included in the AI's system prompt.
        
        Features:
        * Record data serialization (all non-binary fields) via _ai_serialize_fields_data()
        * Chatter history injection via _ai_serialize_messages_data()
        * Automatic context building on session creation via _build_record_context()
        * Works with existing chat, channel, powerbox, and manual Quest types
        * Systray button with quest selector dialog for launching Quests from any form view
        * Robust context capture from HTTP request, env.context, and explicit API
        * JSONRPC endpoints for frontend context push and quest launch
        
        Ported patterns from Odoo Enterprise ai module:
        - models.py: _ai_serialize_fields_data(), _ai_initialise_context()
        - mail_thread.py: _ai_serialize_messages_data()
        - discuss_channel.py: create_ai_draft_channel() context building
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_context',
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3',
    'depends': [
        'ai_agent_core',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_quest_views.xml',
        'views/ai_quest_session_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_agent_context/static/src/js/quest_systray.js',
            'ai_agent_context/static/src/js/form_controller_patch.js',
            'ai_agent_context/static/src/xml/quest_systray.xml',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': False,
}
