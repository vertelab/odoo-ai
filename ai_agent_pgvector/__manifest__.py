# -*- coding: utf-8 -*-
##############################################################################
#
#    Odoo SA, Open Source Management Solution, third party addon
#    Copyright (C) 2025- Vertel Sverige AB (<https://vertel.se>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

{
    'name': 'ai_agent: pg_vector-connector',
    'version': '1.0.3',
    'summary': 'Ao Orchestration and memory using pg_vector',
    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/14.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'other',
    'description': """
        Mempry Pg_vector
        
        pip install  langchain_postgres
        
        sudo apt install postgresql-1[4567]-pgvector  (psql --version)
        sudo -u postgres psql
        CREATE EXTENSION vector;
        SELECT * FROM pg_extension WHERE extname = 'vector';
    """,
    #'sequence': '1',
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_pgvector',
    'images': ['static/description/banner.png'],  # 560x280 px.
    'license': 'AGPL-3',
    'contributor': '',
    # Any module necessary for this one to work correctly
    'depends': [
        'ai_agent',
    ],
    'data': ["security/ir.model.access.csv" ,"views/ai_memory_views.xml", "views/ai_quest_views.xml"],
    # PENSIONERAD (ai-memory-pgvector T6.2, 2026-10-30).
    #
    # Modulen får inte installeras. Skälen:
    #   1. Den interpolerar dokumentinnehåll rakt in i SQL-strängar:
    #        INSERT INTO documents (content, embedding, metadata)
    #        VALUES ('{document.page_content}', ...)
    #      Ett citattecken i en PDF kraschar satsen; ett ont citattecken
    #      injicerar.
    #   2. Den skapar en OHANTERAD tabell `documents` utanför ORM:en —
    #      avinstallation städar aldrig upp den.
    #   3. Den kräver superuser för CREATE EXTENSION och faller med
    #      UserError annars — fel modell för Ubuntu 24.04 + LXD.
    #   4. Den bygger på langchain_postgres + OpenAIEmbeddings, en ANDRA
    #      embedding-väg vid sidan av ai.provider._get_embedding().
    #
    # pgvector-stödet bor nu i ai_agent_core (fields/pg_vector.py +
    # ai.memory.chunk), med samma PgVector-typ som ai.okf.concept använder.
    'installable': False,
    'auto_install': False,
    'application': False,
    "external_dependencies": {
        "python": ["pgvector", "numpy"],
    },
    "pre_init_hook": "pre_init_hook",
}
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: