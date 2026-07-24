# -*- coding: utf-8 -*-
{
    'name': 'AI Agent Core — Academic Research Skills',
    'version': '1.0',
    'summary': 'Academic paper writing pipeline — 12-agent team for research to publication',
    'category': 'AI Orchestration',
    'description': """
        Adds academic research skills to ai_agent_core:
        - academic-paper: 12-agent pipeline (plan/outline/draft/review/format)
        - academic-paper-reviewer: 7-agent review team
        - Pre-built ai.quest "Academic Paper Writer"
        - 24 specialized agents across 3 domains
    """,
    'author': 'Vertel AB',
    'license': 'AGPL-3',
    'depends': ['ai_agent_core'],
    'data': [
        'security/ir.model.access.csv',
        'data/skill_academic_paper.xml',
        'data/agents_academic_paper.xml',
        'data/quest_academic_paper.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
