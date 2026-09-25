{
    'name': 'AI Agent Core — Strategy Skills',
    'version': '18.0.1.0.0',
    'summary': '21 AI skills, 5 agents, 3 quests for business strategy — BMC, SWOT, OKR, Porter, and more.',
    'description': '''
AI Agent Core — Strategy Skills
===============================

    21 AI skills, 5 agents, 3 quests for business strategy — BMC, SWOT, OKR, Porter, and more.

    Features:

        - Focused Fix: A small, targeted improvement to standard Odoo behaviour.
    ''',
    'category': 'AI Orchestration',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_core_strategy',
    'license': 'AGPL-3',
    'depends': ['ai_agent_core'],
    'data': ['security/ir.model.access.csv'],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
    'application': False,
}
