# -*- coding: utf-8 -*-
{
    'name': 'User Mail: AI Assistant',
    'version': '18.0.1.2.0',
    'summary': 'Personlig AI-mailhjälpreda — IMAP-triage, Odoo Mind (graf), Teams→kalender',
    'category': 'Productivity',
    'author': 'Vertel AB',
    'website': 'https://vertel.se',
    'license': 'AGPL-3',
    'description': """
        Personlig AI-hjälpreda för mail (ai.coworker "Mail-hjälpredan").

        - Poller-piggyback: ärver user.mail.imap och konsumerar normaliserade
          mail via _on_new_messages().
        - Triage: user_mail_ai.mail (tunn modell, Message-ID-dedup).
        - OKF-arkiv: varje mail arkiveras i personligt scope (create_from_mail).
        - Graf: :MailMessage-noder + SENT_BY-kant till :OdooPartner via
          graph.node.definition (befintlig 5-min-sync).
        - Klassificering: zero-shot (kategori, action, intresse, teams_invite)
          + deterministisk Teams-detektering.
        - Teams-inbjudan → calendar.event (autonomt, låg risk) + notis.
        - Nudges: Odoo-notis + Discuss-DM (när bot-användare konfigurerad).
    """,
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
