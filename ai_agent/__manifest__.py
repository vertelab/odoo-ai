# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) {year} {company} (<{mail}>)
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
#
# https://www.odoo.com/documentation/14.0/reference/module.html
#
{
    'name': "odoo-ai: AI Agent",
    'version': "1.0",
    'summary': "AI Agent orchestration",
    'category': "AI Orchestration",
    'description': """
        
AI Agent Orchestration is the process of managing and coordinating multiple specialized AI agents to achieve complex tasks and shared objectives. 
This approach allows for the seamless collaboration of various AI agents, each designed for specific functions, to work together efficiently and effectively.

An AI agent is a software system that uses artificial intelligence techniques to interpret information, make decisions, and take actions. 
These agents can be specialized for particular tasks, powered by large language models (LLMs), and equipped with memory capabilities 
(both short-term and long-term, including retrieval-augmented generation or RAG). 
They also have access to various tools to interact with their environment and accomplish their goals.

In this implementation theese
objectives is called Quests. A Quest could be an AI-assistant, an autonous working AI-staff or other things. 


## Key Aspects of AI Orchestration

1. **Task Allocation**: Assigning tasks to the most suitable AI Quest based on their specialized capabilities. Initialaztion could be that something happens, for example mail

2. **Communication**: Enabling effective communications channels, specialiaced AI Chat bots och chatting with an Odoo object (eg helddesk ticket or project task).

3. **Performance Monitoring**: Continuously tracking individual and system-wide performance.

## Motivation for AI Agent Orchestration

- **Enhanced Efficiency**: By leveraging the strengths of multiple specialized agents, organizations can tackle complex challenges more effectively than with a single agent.

- **Scalability**: Orchestration allows for the seamless integration of additional agents as needed, enabling systems to handle increasing workloads and complexity.

- **Flexibility**: The ability to combine different types of agents (e.g., simple reflex, goal-based, learning agents) allows for more adaptable and robust AI systems.

## Integration with Business Systems

Integrating AI agent orchestration with business systems like Odoo can be achieved without exporting sensitive data. This approach ensures data privacy and 
security while still leveraging the power of AI for business processes. AI Quests can deliver the result directly in the ERP-system.

## LLM Agnosticism and Open Source Models

Being LLM-agnostic and utilizing open-source models is crucial for:

1. **Flexibility**: Allows organizations to switch between different LLMs based on their needs and performance.
2. **Cost-effectiveness**: Open-source models can reduce dependency on proprietary solutions.
3. **Customization**: Enables fine-tuning models for specific business needs without vendor lock-in.

## Cost Monitoring and Corporate-wide AI Usage

Tracking token usage and monitoring AI utilization across the organization is essential for:

1. **Cost Management**: Understanding and controlling expenses related to AI usage.
2. **Resource Allocation**: Optimizing the distribution of AI resources based on usage patterns and needs.
3. **Performance Evaluation**: Assessing the effectiveness and efficiency of AI implementations.

By implementing AI agent orchestration with these considerations in mind, organizations can create powerful, flexible, 
and cost-effective AI systems that drive innovation and efficiency across their operations.

        sudo apt-get install graphviz graphviz-dev

        pip install "good to have"
            langchain_openai
            langchain_mistralai
            langchain_groq
            langchain_anthropic
            langchain_huggingface

    """,
    'author': "Vertel AB",
    'website': "https://vertel.se/apps/odoo-ai/ai_agent",
    'images': ["static/description/banner.png"],  # 560x280
    "license": "AGPL-3",
    "depends": [
        "mail",
        "product",
        "crm",
        "queue_job",
        "web_widget_mermaid_field"
    ],
    "data": [
        "security/ai_agent_security.xml",
        "security/ir.model.access.csv",
        "data/server_action.xml",
        "data/data.xml",
        "data/open_ai_data.xml",
        "data/mistral_data.xml",
        "data/anthropic_data.xml",
        "data/azure_data.xml",
        "data/berget_ai_data.xml",
        "data/grok_data.xml",
        "data/groq_data.xml",
        "data/google_data.xml",
        "data/huggingface_data.xml",
        "data/ai_agent_data.xml",
        "data/ai_tool_data.xml",
        "data/ollama_data.xml",
        "data/nebius_data.xml",
        "wizard/ai_agent_test_wizard_views.xml",
        "wizard/ai_quest_test_mail_wizard_views.xml",
        "wizard/ai_memory_test_wizard_views.xml",
        "views/ai_quest_views.xml",
        "views/ai_agent_views.xml",
        "views/ai_agent_llm_views.xml",
        "views/ai_quest_session_views.xml",
        "views/ai_quest_session_line_views.xml",
        "views/ai_quest_session_message_views.xml",
        "views/product_attribute_value_views.xml",
        "views/ai_memory_views.xml",
        "views/ai_tool_views.xml",
        "views/product_template_views.xml",
        "views/res_company_views.xml",
        "views/res_users_views.xml",
        "views/mail_channel_views.xml",
        "views/res_config_settings_views.xml",
        "security/ai_quest_record_rule.xml",
    ],
    "external_dependencies": {
        #"bin": ["postgresql-16-pgvector"], 
        "python": [
            "IPython",
            "bs4",
            "httpx",
            "langchain",
            "langchain_community",
            "langchain_core",
            "langchain_text_splitters",
            "markupsafe",
            "pydantic",
            "secrets",
            "tenacity",
            #"typing",
            #"typing_extensions",
            "faiss-cpu",
            "httpx",
            "importlib",
            "markdown",
            "markdownify",
            "pymupdf",
            "requests",
            "unidecode",
            "uuid",
        ],
    },
    'assets': {
        'web_editor.assets_wysiwyg': [
            'ai_agent/static/src/js/wysiwyg/wysiwyg.js',

            # widgets
            'ai_agent/static/src/js/components/quest_dialog.js',
            'ai_agent/static/src/js/components/quest_prompt_dialog.js',
            'ai_agent/static/src/js/components/quest_prompt_dialog.xml',

            'ai_agent/static/src/js/components/quest_selector_dialog.xml',
            'ai_agent/static/src/js/components/quest_selector_dialog.js',
        ],
    },
    "demo": [
        "demo/ai_agent_demo.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
    # "post_init_hook": "post_init_hook",
}
