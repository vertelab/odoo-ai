# -*- coding: utf-8 -*-
{
    'name': 'AI: Gamification Bridge',
    'version': '18.0.1.0.0',
    'summary': 'Connect gamification (badges, challenges) with AI personal goals.',
    'description': '''
Gamification AI Bridge
======================

    Connect gamification (badges, challenges) with AI personal goals.

    Features:

        - Extends Odoo: Builds on ai.personal.goal.
    ''',
    'category': 'AI/Gamification',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/gamification_ai',
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
