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
    "name": "AI Agents",
    "version": "1.0",
    "summary": "AI Agent orchestration",
    "category": "Productivity",
    "description": """
        Long description of module's purpose
    """,
    "author": "Vertel AB",
    "website": "https://vertel.se/apps/odoo-",
    "images": ["static/description/banner.png"],  # 560x280
    "license": "AGPL-3",
    "depends": ["mail", "product"],
    "data": [
        "security/ir.model.access.csv",
        "data/data.xml",
        "data/open_ai_data.xml",
        "data/mistral_data.xml",
        "wizard/ai_agent_test_wizard_views.xml",
        "views/ai_agent_views.xml",
        "views/ai_agent_llm_views.xml",
        "views/ai_quest_views.xml",
        "views/ai_quest_session_views.xml",
        "views/ai_quest_session_line_views.xml",
        "views/product_template_views.xml",
    ],
    "external_dependencies": {
        "python": [
            "langchain-core",
        ],
    },
    "demo": [],
    "application": False,
    "installable": True,
    "auto_install": False,
    # "post_init_hook": "post_init_hook",
}
