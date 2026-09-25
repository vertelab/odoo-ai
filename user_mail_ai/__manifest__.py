# -*- coding: utf-8 -*-
{
    'name': 'User Mail: AI Assistant',
    'version': '18.0.1.2.0',
    'summary': 'Personlig AI-mailhjälpreda — IMAP-triage, Odoo Mind (graf), Teams→kalender',
    'category': 'Productivity',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/user_mail_ai',
    'license': 'AGPL-3',
    'description': '''
AI Assistant
============

    Personal AI assistant for mail (ai.coworker "Mail Assistant").

    - Poller piggyback: inherits user.mail.imap and consumes normalised mail
      via _on_new_messages().
    - Triage: classifies incoming mail and suggests actions.
    ''',
    'depends': [
        'user_mail_imap',
        'user_mail_common',
        'ai_agent_core',
        'calendar',
    ],
    'external_dependencies': {
        'python': ['icalendar'],
    },
    'data': [
        'security/ir.model.access.csv',
        'security/rules.xml',
        'security/rules_ai.xml',
        'data/mail_coworker.xml',
        'data/mail_routing.xml',
        'data/mail_rules_defaults.xml',
        'data/mail_intelligence_skills.xml',
        'data/cron_intelligence.xml',
        'data/graph_mail_node.xml',
        'views/user_mail_ai_mail_views.xml',
        'views/user_mail_ai_rule_views.xml',
        'views/res_users_mail_ai_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
