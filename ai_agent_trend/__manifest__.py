# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo SA, Open Source Management Solution, third party addon
#    Copyright (C) 2025- Vertel AB (<https://vertel.se>).
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
    'name': 'odoo-ai: Agent Trend Analysis',
    'version': '0.3',
    'summary': 'Agent for analysing trends an create material for blogs and Linkedin-articles',
    'category': 'Productivity / Discuss',
    'description': """
        Agent for analysing trends an create material for blogs and Linkedin-articles
        
    """,
    #'sequence': '1',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_trend',
    'images': ['static/description/banner.png'],  # 560x280 px.
    'license': 'AGPL-3',
    'contributor': '',
    'maintainer': 'Vertel AB',
    # Any module necessary for this one to work correctly
    'depends': [
        'ai_agent_pgvector',
    ],
    'data': [
        # ~ 'data/ai_agent_data.xml',
        'security/ir.model.access.csv',
        'views/ai_trend_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
