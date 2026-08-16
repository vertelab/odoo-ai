# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2026 Vertel Sverige AB (<https://vertel.se>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
{
    'name': 'odoo-ai: Powerbox & Transcript',
    'version': '18.0.1.0.0',
    'summary': 'Powerbox-style AI interaction with transcript/context injection',
    'category': 'AI Orchestration',
    'description': """
        AI — Powerbox & Transcript
        ===========================
        Powerbox-mönstret portat till ai_agent_core-runtime:
        interface-points (HTML-fält, mail-composer, chatter-knapp,
        systray, rösttranskription) → ai.coworker med automatisk
        transcript/context-injektion.

        Komponenter:
        1. ai.composer — mappar interface-keys till ai.coworker med
           default-prompt + quick-actions.
        2. Transcript-injektion — session får rekordfält-JSON + chatter +
           frontend-kontext + vald text (transcript_context).
        3. Mötestext — voice_transcription_component sammanfattar
           rösttranskriptioner till mötesanteckningar/action items.
        4. Powerbox-init (ai_agent_core) kopplas via composer.

        Bridge-standard: depends ai_agent_core (inte legacy ai_agent).
    """,
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_transcript',
    'license': 'AGPL-3',
    'depends': [
        'ai_agent_core',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ai_composer_data.xml',
        'views/ai_composer_views.xml',
        'views/ai_session_views.xml',
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
