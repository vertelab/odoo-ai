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
    'name': 'ai_agent: pg_vector-connector',
    'version': '1.0.3',
    'summary': 'Ao Orchestration and memory using pg_vector',
    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'other',
    'description': """
        Mempry Pg_vector
        
        pip install  langchain_postgres
        
        sudo apt install postgresql-1[4567]-pgvector  (psql --version)
        sudo -u postgres psql
        CREATE EXTENSION vector;
        SELECT * FROM pg_extension WHERE extname = 'vector';
    """,
    #'sequence': '1',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_pgvector',
    'images': ['static/description/banner.png'],  # 560x280 px.
    'license': 'AGPL-3',
    'contributor': '',
    'maintainer': 'Vertel AB',
    # Any module necessary for this one to work correctly
    'depends': [
        'ai_agent',
    ],
    'data': ["security/ir.model.access.csv" ,"views/ai_memory_views.xml", "views/ai_quest_views.xml"],
    'installable': True,
    'auto_install': False,
    'application': False,
    "external_dependencies": {
        "python": ["pgvector", "numpy"],
    },
    "pre_init_hook": "pre_init_hook",
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: