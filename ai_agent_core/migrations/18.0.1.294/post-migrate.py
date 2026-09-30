# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.294: Office-agenten på default-medarbetaren.

(office-document-agent 5.2)

VARFÖR: `default_coworker.xml` ligger i ett `noupdate="1"`-block. Data-XML
körs därför vid INSTALL men inte vid UPDATE — en befintlig installation
skulle aldrig få den nya Office-agenten. Denna migrering gör samma arbete
för befintliga installationer, idempotent.

Skapar:
  - ai.skill "office-dokument" om den saknas (data-XML gör det vid install)
  - ai.agent "Office-dokument" med runtime=external
  - ai.coworker.agent-länk till default-medarbetaren

Rör inga befintliga agenter eller länkar. Kör om utan effekt.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.294: Office-agenten")

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})

    # 1. Default-medarbetaren (adoptera legacy om den finns)
    coworker = env['ai.coworker'].search([('is_default', '=', True)], limit=1)
    if not coworker:
        _logger.info("Ingen default-medarbetare — hoppar över (ny installation "
                     "får agenten via data-XML)")
        return

    # 2. Skillen (data-XML skapar den vid install; här för befintliga)
    skill = env['ai.skill'].search([('name', '=', 'office-dokument')], limit=1)
    if not skill:
        _logger.info("Skillen 'office-dokument' saknas — skapas av data-XML "
                     "vid nästa uppdatering, hoppar över agenten")
        return

    # 3. Agenten
    agent = env['ai.agent'].search([('name', '=', 'Office-dokument')], limit=1)
    if not agent:
        tool_names = [
            'describe_model', 'odoo_search', 'odoo_create',
            'odoo_write', 'odoo_attach', 'odoo_call_method',
        ]
        tools = env['ai.tool'].search([('name', 'in', tool_names)])
        agent = env['ai.agent'].create({
            'name': 'Office-dokument',
            'ai_role': 'Office Document Specialist',
            'description': (
                'Dokumentexpert: läser, skapar och ändrar Office-dokument '
                '(docx, xlsx, pptx, odt, ods, odp) och PDF, inklusive OCR av '
                'skannade PDF:er. Hämtar och lämnar dokument via odoo_attach '
                '— modellagnostiskt (ir.attachment, dms.file eller vilken '
                'modell som helst med ett binärfält).'),
            'status': 'active',
            'runtime': 'external',
            'skill_ids': [(6, 0, [skill.id])],
            'tool_ids': [(6, 0, tools.ids)],
        })
        _logger.info("Skapade agent 'Office-dokument' (id=%s, %d verktyg)",
                     agent.id, len(tools))
    else:
        # Idempotent: säkerställ runtime + verktyg utan att röra egna val
        if agent.runtime != 'external':
            agent.runtime = 'external'
        missing_tools = env['ai.tool'].search([
            ('name', 'in', ['odoo_attach']),
        ]) - agent.tool_ids
        if missing_tools:
            agent.write({'tool_ids': [(4, t.id) for t in missing_tools]})
        if skill not in agent.skill_ids:
            agent.write({'skill_ids': [(4, skill.id)]})

    # 4. Länken till default-medarbetaren
    link = env['ai.coworker.agent'].search([
        ('coworker_id', '=', coworker.id),
        ('agent_id', '=', agent.id),
    ], limit=1)
    if not link:
        env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'role': 'member',
            'sequence': 40,
        })
        _logger.info("Länkade 'Office-dokument' till '%s'", coworker.name)
    else:
        _logger.info("Länken finns redan — inget att göra")
