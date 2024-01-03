# -*- coding: utf-8 -*-
{
    'name': 'Chat AI-Bot Common',
    'summary': 'Make OdooBot finally useful. Create users that you chat with just like ChatGPT. Integrate with OpenAI ChatGPT (GPT-3) or other LLM',
    'description': """Make OdooBot useful by adding OpenAI GPT intelligence """,
    'license': 'AGPL-3',
    'version': '16.0.1.0.3',
    'category': 'Productivity/Discuss',
    'author': 'Vertel AB',
    'website': 'https://vertel.se',

    'depends': [
        'mail',
    ],


    'data': [
        'security/res_groups.xml',
        'security/ir.model.access.csv',

        'views/res_users.xml',

        'views/openai_log.xml',

        'views/menu.xml',
    ],

#    'images': [
#        'static/description/cover/odoogpt.png',
#    ],

    'installable': True,
    'auto_install': False,
    'application': False,
}
