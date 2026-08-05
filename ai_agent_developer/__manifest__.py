# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'odoo-ai: Developer Coworker',
    'version': '18.0.1.0',
    'summary': 'AI coworker specialized in Odoo development — accessible via Pi API',
    'category': 'AI Orchestration',
    'description': """
        AI Developer Coworker
        =====================
        
        Adds "Odoo-Developer" — an AI coworker specialized in Odoo 18/19
        development. Accessible via Pi's OpenAI-compatible API (/ai/v1/) and
        the Odoo web UI chat.
        
        Expertise:
        * Odoo ORM (models, fields, computed fields, constraints)
        * View architecture (form, list, kanban, search, graph, pivot)
        * QWeb templates and OWL 2 components
        * Security (ir.model.access, record rules, groups)
        * Wizards and server actions
        * Module structure and manifests
        * Migrations and hooks
        * SaltStack deployment and checkmodule
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_developer',
    'license': 'AGPL-3',
    'depends': [
        'ai_agent_core',
    ],
    'data': [
        'data/coworker_developer.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
