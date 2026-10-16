# -*- coding: utf-8 -*-
{
    'name': 'AI Agent — SEO & GEO',
    'version': '18.0.1.0.0',
    'category': 'Marketing',
    'summary': 'SEO & GEO-specialist: agent, skills och verktyg för sökmotor- '
               'och generativ motor-optimering (llms.txt, JSON-LD, SERP).',
    'description': """
AI Agent — SEO & GEO
====================

Lägger en specialistagent på den allmänna assistenten (coworkern
"Allmän assistent") med kunskap och verktyg för:

SEO (klassisk sökmotoroptimering)
  - Metatitel, beskrivning, rubrikstruktur, interna länkar
  - Sökresultat-positioner (SERP) via base_serp

GEO (Generativ Engine Optimization)
  - llms.txt / llms-full.txt (llmstxt.org)
  - JSON-LD / strukturerad data (schema.org)
  - AI-citering, self-contained passager, verifierbarhet

Innehåller:
  - ai.agent "SEO & GEO-specialist"
  - ai.skill: seo-geo (huvudmetodik), llms-txt, structured-data
  - ai.tool: seo_audit_url, seo_llms_txt, seo_json_ld_check
  - ai.tool: seo_serp_lookup (via base_serp om installerad)

Agenten kopplas till coworkern "Allmän assistent" via en
ai.coworker.agent-rad (role=member) — den syns därmed i chatten.

Referensimplementation för GEO i Odoo: modulen arc_open_business
(controllers/arc_ob_llms.py, models/arc_ob_seo.py).
    """,
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se',
    'license': 'AGPL-3',
    'depends': [
        'ai_agent_core',
    ],
    'external_dependencies': {
        'python': [
            'requests',
            'beautifulsoup4',
        ],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/seo_skills.xml',
        'data/seo_tools.xml',
        'data/seo_agent.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
