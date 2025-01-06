# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo SA, Open Source Management Solution, third party addon
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
    'name': 'Agent Orchestration HR',
    'version': '0.3',
    'summary': 'Agent Orchestration HR',
    'category': 'Productivity / Discuss',
    'description': """
        Agent Orchestration for departments
        Organize you AI Staff
    """,
    #'sequence': '1',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_hr',
    'images': ['static/description/banner.png'],  # 560x280 px.
    'license': 'AGPL-3',
    'contributor': '',
    'maintainer': 'Vertel AB',
    'repository': 'https://github.com/vertelab/odoo-ai/ai_business_intelligence',
    # Any module necessary for this one to work correctly
    'depends': [
        'ai_agent',
        'hr',
    ],
    'data': [
        'data/ai_agent_data.xml',
        'views/hr_department_views.xml',
        'views/ai_quest_views.xml',
        'views/ai_quest_session_views.xml',
        'views/ai_quest_session_line_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
