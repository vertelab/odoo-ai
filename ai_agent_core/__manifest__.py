# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
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
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

{
    'name': 'odoo-ai: AI Agent Core',
    'version': '1.0',
    'summary': 'Core AI agent engine with SSE streaming, provider management, and plugin architecture',
    'category': 'AI Orchestration',
    'description': """
        AI Agent Core — the foundation for AI-powered agents in Odoo.
        
        Features (prototype):
        * SSE streaming endpoint for real-time AI responses
        * Provider abstraction layer (WIP)
        * Agent loop engine (WIP)
        * Plugin architecture for extensions
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_core',
    'license': 'AGPL-3',
    'depends': [
        'ai_agent',
        'mail',
    ],
    'data': [
        'views/templates.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
