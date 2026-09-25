# -*- coding: utf-8 -*-
{
    'name': 'AI Agent Zabbix',
    'version': '18.0.1.0.0',
    'license': 'AGPL-3',
    'category': 'AI',
    'summary': 'Zabbix integration for AI agent monitoring.',
    'description': '''
AI Agent Zabbix
===============

    Zabbix integration for ai_agent_core.
    Sends Zabbix events when quests exceed systemtoken caps.
    Uses Zabbix 7.0 JSON-RPC API.

    Depends on:
        - ai_agent_core: quest cap enforcement triggers
        - Zabbix 7.0 server with API token (configured in pillar)

    Features:

        - UI Integration: Extends 1 view(s) in the Odoo interface.
        - Extends Odoo: Builds on ai.zabbix.config.
    ''',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_zabbix',
    'depends': ['ai_agent_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/zabbix_data.xml',
        'views/ai_zabbix_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
