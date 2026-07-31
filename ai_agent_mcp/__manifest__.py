# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) {year} {company} (<{mail}>)
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
#
# https://www.odoo.com/documentation/14.0/reference/module.html
#

{
    'name': "odoo-ai: MCP Resource Management",
    'version': "1.0",
    'summary': "Adds MCP Power to AI Agent and AI Quests",
    'category': "AI Orchestration",
    "description": "Module to manage 'res.mcp' resources with interface and security rules.",
    "author": "Vertel AB",
    'website': "https://vertel.se/apps/odoo-ai/ai_agent",
    'images': ["static/description/banner.png"],  # 560x280
    "license": "AGPL-3",
    "depends": ["fastapi", "ai_agent", "ai_agent_core"],
    'external_dependencies': {
        'python': ['fastapi-mcp']
    },
    "data": [
        'data/fastapi_data.xml',
        'data/tools_data.xml',
        'data/ai_agent_data.xml',
        'data/ai_quest_data.xml',
        "security/ir.model.access.csv",
        "security/res_mcp_security.xml",
        "views/res_mcp_views.xml",
        'views/ai_quest_view.xml',
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
