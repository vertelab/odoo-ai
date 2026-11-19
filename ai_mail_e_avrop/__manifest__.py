# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo SA, Open Source Management Solution, third party addon
#    Copyright (C) 2024- Vertel Sverige AB (<https://vertel.se>).
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
    'name': 'AI: Mailbox',
    'version': '18.0.1.0.1',
    # Version ledger: 16.0 = Odoo version. 1 = Major. Non regressionable code. 2 = Minor. New features that are regressionable. 3 = Bug fixes
    'summary': 'Mailbox for AI.',
    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Productivity / Discuss',
    'description': '''
Mailbox for AI
==============

    Mailbox for AI.

    Features:

        - UI Integration: Extends 3 view(s) in the Odoo interface.
        - Extends Odoo: Builds on ai.agent, ai.quest, ai.quest.session, crm.lead.
    ''',
    #'sequence': '1',
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_mail_e_avrop',
    'images': ['static/description/banner.png'],  # 560x280 px.
    'license': 'AGPL-3',
    'contributor': '',
    'repository': 'https://github.com/vertelab/odoo-ai',
    # Any module necessary for this one to work correctly
    'depends': [
        'mail',
        'base',
        'sales_team',
        'crm',
        "ai_agent"
    ],
    'data': [
        #'security/ir.model.access.csv',
        #'views/mail_ai_views.xml',
        #'views/mail_channel_ai_views.xml',
        #'views/res_config_settings_views.xml',
        #'views/crm_lead_view.xml',
        #'views/menu.xml',
        'data/ai_agent_data.xml'

    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
