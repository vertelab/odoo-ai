# -*- coding: utf-8 -*-
"""Post-install hook: create academic paper writing quest and agents."""

import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

AGENTS = [
    ('agent_intake', 'Intake Agent', 'Configuration interview. Paper type, discipline, citation format, output format.'),
    ('agent_literature', 'Literature Strategist', 'Phase 1: Systematic literature search strategy, source screening, annotated bibliography.'),
    ('agent_structure', 'Structure Architect', 'Phase 2: Paper architecture (IMRaD/Thematic), outline, word count allocation, evidence mapping.'),
    ('agent_argument', 'Argument Builder', 'Phase 3: Claim-evidence chains, logical flow, supporting evidence verification.'),
    ('agent_draft_writer', 'Draft Writer', 'Phase 4: Full-text draft, section by section, academic register.'),
    ('agent_citation', 'Citation Compliance', 'Phase 5: Citation verification against sources, format compliance (APA/Chicago/MLA/IEEE/Vancouver).'),
    ('agent_peer_reviewer', 'Peer Reviewer', 'Phase 6: Five-dimension review (methodology, argumentation, clarity, contribution, reproducibility).'),
    ('agent_formatter', 'Formatter', 'Phase 7: Output formatting (LaTeX, DOCX via Pandoc, PDF, Markdown).'),
]


def post_init_hook(cr, registry):
    """Create agents and quest after module installation."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Create skill
    skill = env['ai.skill'].create({
        'name': 'Academic Paper Writing',
        'description': '12-agent academic paper writing pipeline. 11 modes. 6 paper types, 5 citation formats.',
        'category': 'general',
        'compatibility': 'any',
        'trigger_keywords': 'write paper, academic paper, revise paper, literature review, 논문, 學術論文',
    })
    _logger.info('Created skill: %s', skill.name)

    # Create agents
    agents = {}
    for xmlid, name, desc in AGENTS:
        agent = env['ai.agent'].create({
            'name': name,
            'description': desc,
            'provider_type': 'bifrost',
            'bifrost_model': 'cerebras/gpt-oss-120b',
            'status': 'active',
        })
        agents[xmlid] = agent

    # Create quest
    quest = env['ai.quest'].create({
        'name': 'Academic Paper Writer',
        'description': 'Academic paper writing supervisor. Coordinates 8 agents through 7-phase pipeline.',
        'sub_description': '8-agent pipeline — research to publication',
        'init_type': 'manual',
        'is_supervisor': True,
        'status': 'active',
        'use_chat_history': True,
        'use_time_context': True,
    })

    # Assign agents in sequence
    for seq, (xmlid, agent) in enumerate(agents.items(), 1):
        env['ai.quest.agent'].create({
            'quest_id': quest.id,
            'agent_id': agent.id,
            'sequence': seq,
        })

    _logger.info('Created quest "%s" with %d agents', quest.name, len(agents))
