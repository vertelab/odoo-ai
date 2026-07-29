# -*- coding: utf-8 -*-
{
    'name': 'Gamification AI Bridge',
    'version': '18.0.1.0.0',
    'summary': 'Connect gamification (badges, challenges) with AI personal goals',
    'category': 'AI/Gamification',
    'author': 'Vertel AB',
    'license': 'AGPL-3',
    'depends': ['ai_agent_core', 'gamification'],
    'data': [
        'data/badge_rules.xml',
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
