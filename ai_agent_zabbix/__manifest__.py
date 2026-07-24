# -*- coding: utf-8 -*-
{
    'name': 'AI Agent Zabbix',
    'version': '18.0.1.0.0',
    'category': 'AI',
    'summary': 'Zabbix integration for AI agent monitoring',
    'description': """
Zabbix integration for ai_agent_core.
Sends Zabbix events when quests exceed systemtoken caps.
Uses Zabbix 7.0 JSON-RPC API.

Depends on:
    - ai_agent_core: quest cap enforcement triggers
    - Zabbix 7.0 server with API token (configured in pillar)
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se',
    'depends': ['ai_agent_core'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_zabbix_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
