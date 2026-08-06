# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

{
    'name': 'agent_pi — Pi Agent Infrastructure',
    'version': '0.2.0',
    'summary': 'Distributed Pi Coding Agent orchestration via NATS',
    'category': 'AI Orchestration',
    'description': """
        Odoo module for distributed Pi Coding Agent orchestration via NATS messaging.

        Models:
        - pi.skill — Agent skills (Markdown instructions)
        - pi.skill.category — Skill categories
        - pi.task — Agent tasks with priority, retry, artifacts
        - pi.agent — Registered Pi agents with health monitoring
        - pi.artifact — Task results, logs, images

        Communication:
        - NATS publish/subscribe via nats-py
        - Callback endpoint /pi/callback/<task_id>
        - JetStream for persistence, retry, dead-letter

        Used by:
        - module_quality v2 (container-based quality checks)
        - Future: automated deployments, monitoring, code review
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/agent_pi',
    'license': 'AGPL-3',
    'depends': ['mail', 'base'],
    'external_dependencies': {
        'python': ['nats', 'markdown'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/pi_skill_data.xml',
        'data/svensk_bokforing_skills.xml',
        'data/pm_skills.xml',
        'views/res_config_settings_views.xml',
        'views/pi_skill_views.xml',
        'views/pi_task_views.xml',
        'views/pi_agent_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
