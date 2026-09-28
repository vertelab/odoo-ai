# -*- coding: utf-8 -*-
{
    'name': 'Pi Agent Mesh',
    'version': '18.0.1.6.0',
    'license': 'AGPL-3',
    'category': 'AI',
    'summary': 'Historik och överblick för Pi-agenternas mesh',
    'description': """
Pi Agent Mesh — historik för agent-till-agent-meshen.

Transporten är NATS (se /srv/salt/pi). Den här modulen är HISTORIKEN:
agenter, meddelanden, lås och uppgifter som Odoo-poster, med en kanban
över agenter och deras pågående arbete.

VARFÖR: NATS-meddelanden är flyktiga. När två agenter kolliderar om en
fil finns inget kvar att titta på efteråt — vem rörde filen, när, och
vem varnades? Meshen behöver ett minne som överlever sessionen.

Mönstret är detsamma som saltstack.alert: en webhook tar emot
händelser, Bearer-token via hmac.compare_digest, alltid 200 så att en
trasig avsändare inte fastnar i retry-loopar.

Depends on:
    - ai_agent_core: coworker- och sessionsmodellerna
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se',
    'depends': ['ai_agent_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/pi_mesh_data.xml',
        'views/pi_mesh_agent_views.xml',
        'views/pi_mesh_message_views.xml',
        'views/pi_mesh_lock_views.xml',
        'views/pi_mesh_task_views.xml',
        'views/pi_mesh_menu.xml',
    ],
    'installable': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
}
