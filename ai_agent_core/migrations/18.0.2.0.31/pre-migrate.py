# -*- coding: utf-8 -*-
"""Pre-migration 18.0.2.0.31: flytta default-coworkern till bolaget.

VARFÖR: `is_default` på `ai.coworker` tas bort. Flaggan gjorde en
ORGANISATIONSINSTÄLLNING till en egenskap hos en medarbetare och krävde att
exakt en rad bar den. Default-valet bor nu på `res.company`
(`ai_default_coworker_id`) — ett fält per bolag.

Detta är en PRE-migrering: den körs före modell-initieringen, medan
`ai_coworker.is_default`-kolumnen fortfarande finns. En post-migrering hade
varit för sen — kolumnen är borta när den körs.

IDEMPOTENT: bolag som redan har ett default lämnas orörda. Saknas kolumnen
(t.ex. vid en omskörd körning efter att Odoo redan släppt den) faller
migrationen tillbaka på xmlid `ai_agent_core.coworker_default_assistent`.

BOLAGSVALSREGEL: flaggan sattes aldrig per bolag (fältet var globalt), så
värdet skrivs till det bolag som coworkern tillhör — eller till det enda
bolaget i databasen när coworkern saknar company_id. Finns flera bolag och
coworkern är bolagslös skrivs inget: hellre ett tomt fält än en gissning.
"""

import logging

_logger = logging.getLogger(__name__)

XMLID = 'ai_agent_core.coworker_default_assistent'


def _column_exists(cr, table, column):
    cr.execute("""
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _coworker_id_from_flag(cr):
    """Coworkern som bar flaggan, annars None."""
    if not _column_exists(cr, 'ai_coworker', 'is_default'):
        return None
    cr.execute("""
        SELECT id FROM ai_coworker
         WHERE is_default IS TRUE
         ORDER BY id ASC
         LIMIT 1
    """)
    row = cr.fetchone()
    return row[0] if row else None


def _coworker_id_from_xmlid(cr):
    """Fallback: xmlid-bunden default-coworker, annars None."""
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = %s AND name = %s AND model = %s
         LIMIT 1
    """, ('ai_agent_core', 'coworker_default_assistent', 'ai.coworker'))
    row = cr.fetchone()
    return row[0] if row else None


def _company_id_for(cr, coworker_id):
    """Bolaget som ska ärva default-valet, annars None."""
    cr.execute("SELECT company_id FROM ai_coworker WHERE id = %s",
               (coworker_id,))
    row = cr.fetchone()
    if row and row[0]:
        return row[0]
    # Coworkern är bolagslös: bara entydigt när databasen har ett bolag.
    cr.execute("SELECT id FROM res_company ORDER BY id ASC LIMIT 2")
    rows = cr.fetchall()
    if len(rows) == 1:
        return rows[0][0]
    return None


def migrate(cr, version):
    if not _column_exists(cr, 'res_company', 'ai_default_coworker_id'):
        _logger.info(
            'Pre-migration 18.0.2.0.31: res_company.ai_default_coworker_id '
            'saknas — hoppar över')
        return

    coworker_id = _coworker_id_from_flag(cr)
    source = 'is_default'
    if coworker_id is None:
        coworker_id = _coworker_id_from_xmlid(cr)
        source = 'xmlid'
    if coworker_id is None:
        _logger.info(
            'Pre-migration 18.0.2.0.31: ingen default-coworker att flytta')
        return

    company_id = _company_id_for(cr, coworker_id)
    if company_id is None:
        _logger.warning(
            'Pre-migration 18.0.2.0.31: coworker %s är bolagslös och '
            'databasen har flera bolag — lämnar ai_default_coworker_id tomt '
            '(ingen gissning)', coworker_id)
        return

    # Skriv bara om bolaget saknar val (idempotent).
    cr.execute("""
        UPDATE res_company
           SET ai_default_coworker_id = %s
         WHERE id = %s
           AND ai_default_coworker_id IS NULL
    """, (coworker_id, company_id))
    if cr.rowcount:
        _logger.info(
            'Pre-migration 18.0.2.0.31: bolag %s fick default-coworker %s '
            '(källa: %s)', company_id, coworker_id, source)
    else:
        _logger.info(
            'Pre-migration 18.0.2.0.31: bolag %s hade redan ett '
            'default-val — oförändrat', company_id)
