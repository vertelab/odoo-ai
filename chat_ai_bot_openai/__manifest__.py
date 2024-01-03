# -*- coding: utf-8 -*-
{
    'name': 'Chat AI-Bot for OpenAI',
    'summary': 'AI Bot for LLM-servers compatible with OpenAI/using openai-api',
    'description': """AI Bot for LLM-servers compatible with OpenAI/using openai-api """,
    'license': 'AGPL-3',
    'version': '16.0.1.0.3',
    'category': 'Productivity/Discuss',
    'author': 'Vertel AB',
    'website': 'https://vertel.se',

    'depends': [
        'chat_ai_bot_common'
    ],
    'external_dependencies': {
        'python': [
            'openai'
        ]
    },

    'data': [
    ],

#    'images': [
#        'static/description/cover/odoogpt.png',
#    ],

    'installable': True,
    'auto_install': False,
    'application': False,
}
