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
    'name': 'odoo-ai: Powerbox & Transcript',
    'version': '1.0',
    'summary': 'Powerbox-style AI interaction with transcript/context injection',
    'category': 'AI Orchestration',
    'description': """
        AI Agent — Powerbox & Transcript
        =================================
        
        Implements the Powerbox pattern from Odoo Enterprise AI for the
        ai_agent framework. A Powerbox is an interface point (like an HTML
        field, email composer, or chatter button) that triggers an AI Quest
        with automatic transcript/context injection.
        
        Key concepts ported from Odoo Enterprise ai module:
        
        1. AI Composer (ai.composer → ai.composer)
           Maps interface keys to AI Quests with default prompts.
           Each composer defines which Quest runs for a given interface point
           and which models it applies to.
        
        2. Transcript Injection
           When a Powerbox Quest is triggered from a record, the session
           automatically receives:
           - Record fields JSON (all non-binary fields)
           - Chatter message history (chronological)
           - Frontend context (active model, view type, etc.)
           - Selected text (for text-rewrite operations)
        
        3. Powerbox Quest Type
           New INIT_TYPE = 'powerbox' for Quests. These Quests are designed
           to be triggered directly from record form views, chatter buttons,
           and email composers — receiving full record context automatically.
           
        4. Interface Keys (matching Enterprise):
           - html_field_record: Triggered from HTML field's AI button
           - mail_composer: Triggered from email composer
           - html_field_text_select: Rewrite selected text
           - chatter_ai_button: Triggered from chatter's "Ask AI" button
           - systray_ai_button: Triggered from systray AI button
           - voice_transcription_component: Summary from voice transcript
        
        Requirements:
        - ai_agent (core AI orchestration)
        - ai_agent_context (record serialization + chatter history)
        
        Ported from Odoo Enterprise:
        - ai/models/ai_composer.py
        - ai/models/discuss_channel.py (create_ai_draft_channel pattern)
        - ai/models/models.py (_ai_initialise_context pattern)
        - ai/static/src/editor/plugins/chatgpt_plugin.js (powerbox pattern)
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_transcript',
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3',
    'depends': [
        'ai_agent',
        'ai_agent_context',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ai_composer_data.xml',
        'data/ai_quest_data.xml',
        'views/ai_composer_views.xml',
        'views/ai_quest_session_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_agent_transcript/static/src/js/quest_powerbox.js',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': False,
}
