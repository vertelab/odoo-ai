# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "AI Core — LLM Provider Abstraction",
    "version": "18.0.1.0.0",
    "summary": "Provider abstraction, model discovery, rate limiting, token tracking",
    "category": "AI",
    "author": "Vertel Sverige AB",
    "website": "https://vertel.se",
    "license": "AGPL-3",
    "depends": ["mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/llm_provider_views.xml",
        "data/demo_providers.xml",
    ],

    "installable": True,
    "application": False,
    "auto_install": False,
}
