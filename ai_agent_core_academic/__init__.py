# -*- coding: utf-8 -*-
"""Post-install hook: create academic paper writing quest and agents."""

import logging

_logger = logging.getLogger(__name__)

_RECIPE = """# Academic Paper Writing Pipeline

## Overview
You are a supervisor coordinating a team of 8 specialized agents through
a 7-phase academic paper writing pipeline. Your job is to route the user's
request to the right agent at the right phase, verify outputs, and
synthesize the final result.

## Pipeline Phases
1. **Intake** — Configuration interview: paper type, discipline, citation format, output format
2. **Literature Search** — Systematic search strategy, source screening, annotated bibliography
3. **Structure Design** — Paper architecture (IMRaD/Thematic/Theoretical), outline, word count allocation
4. **Argument Building** — Claim-evidence chains (CER), logical flow, supporting evidence verification
5. **Draft Writing** — Full-text draft, section by section, academic register and style
6. **Citation Compliance** — Verify all citations against sources, check format (APA/Chicago/MLA/IEEE/Vancouver)
7. **Formatting** — Output formatting (LaTeX, DOCX via Pandoc, PDF, Markdown)

## Agent Team
- **Intake Agent**: Gather paper requirements and configure pipeline parameters
- **Literature Strategist**: Design search strategy, screen and annotate sources
- **Structure Architect**: Select paper structure, design outline, allocate word counts
- **Argument Builder**: Build claim-evidence chains with logical flow
- **Draft Writer**: Write the full manuscript following the outline
- **Citation Compliance**: Verify all references and citations
- **Peer Reviewer**: Five-dimension quality review
- **Formatter**: Convert to target output format

## Quality Gates
- Each phase output is verified before the next phase begins
- Citations are checked against actual sources
- Peer review assesses methodology, argumentation, clarity, contribution, reproducibility
- Format compliance with target venue requirements

## Modes
- **Full pipeline**: Run all 7 phases sequentially
- **Plan only**: Use Structure Architect for outline guidance
- **Abstract only**: Generate bilingual abstract
- **Revision**: Improve existing draft with reviewer feedback
"""

AGENTS = [
    ('agent_intake', 'Intake Agent',
     'Phase 0: Configuration interview. Determine paper type, discipline, '
     'citation format (APA/Chicago/MLA/IEEE/Vancouver), output format '
     '(LaTeX/DOCX/PDF/Markdown), and writing mode before the pipeline starts.'),
    ('agent_literature', 'Literature Strategist',
     'Phase 1: Design systematic literature search strategy. Screen sources '
     'for relevance and quality. Produce annotated bibliography with key findings.'),
    ('agent_structure', 'Structure Architect',
     'Phase 2: Select optimal paper structure (IMRaD/Thematic/Theoretical). '
     'Design detailed section-by-section outline. Allocate word counts. '
     'Map evidence to sections.'),
    ('agent_argument', 'Argument Builder',
     'Phase 3: Build claim-evidence chains (CER). Construct logical flow '
     'between sections. Ensure every claim has supporting evidence from '
     'the literature report.'),
    ('agent_draft_writer', 'Draft Writer',
     'Phase 4: Write full-text draft section by section following the '
     'outline and argument blueprint. Apply academic writing style and '
     'discipline-specific register.'),
    ('agent_citation', 'Citation Compliance',
     'Phase 5: Verify all citations against sources. Check format compliance '
     'with target citation style. Flag missing, incomplete, or fabricated references.'),
    ('agent_peer_reviewer', 'Peer Reviewer',
     'Phase 6: Five-dimension review: methodology, argumentation, clarity, '
     'contribution, reproducibility. Produce structured review report with '
     'revision suggestions.'),
    ('agent_formatter', 'Formatter',
     'Phase 7: Convert draft to target output format. Apply journal or '
     'conference template. Generate LaTeX, DOCX (via Pandoc), PDF, or Markdown.'),
]


def post_init_hook(env):
    """Create agents and quest after module installation."""

    # Check if already installed (idempotent)
    existing = env['ai.skill'].search_count([('name', '=', 'Academic Paper Writing')])
    if existing:
        _logger.info('Academic paper writing skill already exists — skipping')
        return

    # Create skill with full recipe
    skill = env['ai.skill'].create({
        'name': 'Academic Paper Writing',
        'description': '8-agent academic paper writing pipeline covering the full research-to-publication workflow.',
        'category': 'general',
        'compatibility': 'any',
        'trigger_keywords': 'write paper, academic paper, revise paper, literature review, 논문, 學術論文, paper outline, journal article',
        'recipe_text': _RECIPE,
    })
    _logger.info('Created skill: %s', skill.name)

    # Create agents linked to the skill
    agents = {}
    for xmlid, name, desc in AGENTS:
        agent = env['ai.agent'].create({
            'name': name,
            'description': desc,
            'provider_type': 'bifrost',
            'bifrost_model': 'cerebras/gpt-oss-120b',
            'status': 'active',
            'skill_ids': [(4, skill.id)],
        })
        agents[xmlid] = agent

    # Create quest
    quest = env['ai.quest'].create({
        'name': 'Academic Paper Writer',
        'description': _RECIPE,
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

    _logger.info('Created quest "%s" with %d agents and skill "%s"',
                  quest.name, len(agents), skill.name)
