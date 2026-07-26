# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
##############################################################################

{
    'name': 'odoo-ai: AI Agent Core',
    'version': '1.0',
    'summary': 'Standalone AI agent engine — SSE streaming, provider management, identity, skills, tools',
    'category': 'AI Orchestration',
    'description': """
        AI Agent Core — self-sufficient AI agent platform for Odoo.
        
        Standalone module — does not require ai_agent.
        If ai_agent is installed, integrates with existing quests.
        
        Features:
        * Agent Loop (Buzz-inspired while-loop, no LangChain)
        * BifrostProvider + DirectProvider (OpenAI, Anthropic, DeepSeek)
        * SSE streaming + web chat UI
        * Human-in-the-Loop (Discuss, WebUI, Auto handlers)
        * Agent Identity (SOUL.md — personality, style, values)
        * Skills system (reusable competencies with recipes)
        * Taskless learning layer (detect, route, improve, verify)
    """,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_core',
    'license': 'AGPL-3',
    'depends': [
        'base',
        'mail',
        'html_editor',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/identity_templates.xml',
        'data/cron_monthly_summary.xml',
        'data/cron_bifrost_sync.xml',
        'data/cron_kaizen.xml',
        'data/cron_onboard.xml',
        'data/cron_memory_consolidation.xml',
        'data/cron_scheduled_quests.xml',
        'data/bridge_config.xml',
        'data/youtube_tools.xml',
        'views/ai_monthly_summary_views.xml',
        'views/menu.xml',
        'views/ai_provider_views.xml',
        'views/ai_model_views.xml',
        'views/ai_quest_views.xml',
        'views/ai_agent_views.xml',
        'views/ai_identity_views.xml',
        'views/ai_skill_views.xml',
        'views/ai_tool_views.xml',
        'views/ai_tag_views.xml',
        'views/ai_quest_init_type_views.xml',
        'views/powerbox_templates.xml',
        'views/ai_session_views.xml',
        # 'views/res_config_settings_views.xml',  # xpath broken in Odoo 18, fix later
        'views/templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_agent_core/static/src/js/powerbox.js',
            'ai_agent_core/static/src/css/powerbox.css',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
    'post_init_hook': 'post_init_hook',
}
