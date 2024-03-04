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
    'name': 'Langchain Chat',
    'version': '16.00.0.0',
    # Version ledger: 16.0 = Odoo version. 1 = Major. Non regressionable code. 2 = Minor. New features that are regressionable. 3 = Bug fixes
    'summary': 'Easy Chat using LangChain',
    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Productivity/Discuss',
    'description': """
    A custom chat bot designed to be interpendent and easy to use.
    """,
    #'sequence': '1',
    'author': 'Vertel AB',
    'license': 'AGPL-3',
    'contributor': 'Andreas',
    'maintainer': 'Vertel AB',
    'repository': 'https://github.com/vertelab/odoo-ai',
    'depends': ["base"],
    'data': [
        "security/ir.model.access.csv",
        "views/langchain_chat_view.xml",
        "views/langchain_chat_settings.xml",
        "views/langchain_chat_menu.xml",
        ],
        'external_dependencies': {
        'python': [
            'openai'
        ]
    },
    'installable': True,
    'auto_install': False,
    'application': False,
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
