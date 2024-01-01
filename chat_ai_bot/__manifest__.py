# -*- coding: utf-8 -*-
{
    'name': 'Chat AI-Bot',
    'summary': 'Make OdooBot finally useful. Integrate with OpenAI ChatGPT (GPT-3) or other LLM',
    'description': """Make OdooBot useful by adding OpenAI GPT-3 intelligence """,
    'license': 'AGPL-3',
    'version': '16.0.1.0.3',
    'category': 'Productivity/Discuss',
    'author': 'Vertel AB',
    'website': 'https://vertel.se',

    'depends': [
        'mail_bot','mail',
    ],
    'external_dependencies': {
        'python': [
            'openai'
        ]
    },

    'data': [
        'security/res_groups.xml',
        'security/ir.model.access.csv',

        'views/res_users.xml',

        'views/openai_log.xml',

        'views/menu.xml',
    ],

#    'assets': {
#        'mail.assets_messaging': [
#            'odoogpt/static/src/models/*.js',
#        ],
#    },

#    'images': [
#        'static/description/cover/odoogpt.png',
#    ],

    'installable': True,
    'auto_install': False,
    'application': False,
}
