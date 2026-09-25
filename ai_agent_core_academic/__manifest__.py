# -*- coding: utf-8 -*-
{
    'name': 'AI Agent Core — Academic Research Skills',
    'version': '18.0.1.0.0',
    'summary': 'Academic paper writing pipeline — 8-agent team for research to publication.',
    'description': '''
AI Agent Core — Academic Research Skills
========================================

    Academic paper writing pipeline — 8-agent team for research to publication.

    Features:

        - Focused Fix: A small, targeted improvement to standard Odoo behaviour.
    ''',
    'category': 'AI Orchestration',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_core_academic',
    'license': 'AGPL-3',
    'depends': ['ai_agent_core'],
    'data': ['security/ir.model.access.csv'],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
    'application': False,
}
