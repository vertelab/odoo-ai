# -*- coding: utf-8 -*-
"""Migrate to 1.13: OKF tre-scope-migrering (tasks 6.1–6.5).

- 6.1: ai.company.memory → ai.okf.concept (owner_company_id, tunna koncept)
- 6.2: ai.personal.memory → ai.okf.concept (owner_user_id)
- 6.3: ai.memory med quest_id → ai.okf.concept (owner_coworker_id)
- 6.4: ai.company.memory.category → ai.artifact.type (bevara group_ids)
- 6.5: gamla modeller behålls som read-only (arkiveras i migrationen)

Idempotent: körs endast om okf-koncept saknas för källan.
"""

import logging

_logger = logging.getLogger(__name__)


def _migrate_company_memory(env):
    """6.1: ai.company.memory → company-scope."""
    Company = env['ai.company.memory']
    Concept = env['ai.okf.concept']
    if not Company or not hasattr(Company, 'content'):
        return
    memories = Company.search([])
    count = 0
    for mem in memories:
        key = 'ai.company.memory,%s' % mem.id
        if Concept.search_count([('concept_key', '=', key),
                                 ('scope', '=', 'company')]):
            continue
        atype = None
        cat = mem.category_id
        if cat and hasattr(cat, 'artifact_type_id'):
            atype = cat.artifact_type_id
        Concept._okf_upsert(
            artifact_type=atype or 'knowledge',
            concept_key=key,
            summary=mem.content or '',
            title=mem.content[:80] if mem.content else key,
            source_ref=key,
            owner_company_id=mem.company_id.id or env.company.id,
            generated_by='migration',
        )
        count += 1
    if count:
        _logger.info('OKF migration: %s company memories → concepts', count)


def _migrate_personal_memory(env):
    """6.2: ai.personal.memory → personal-scope."""
    Personal = env['ai.personal.memory']
    Concept = env['ai.okf.concept']
    if not Personal or not hasattr(Personal, 'content'):
        return
    memories = Personal.search([])
    count = 0
    for mem in memories:
        key = 'ai.personal.memory,%s' % mem.id
        if Concept.search_count([('concept_key', '=', key),
                                 ('scope', '=', 'personal')]):
            continue
        if not mem.user_id:
            continue
        Concept._okf_upsert(
            artifact_type='learning',
            concept_key=key,
            summary=mem.content or '',
            title=mem.content[:80] if mem.content else key,
            source_ref=key,
            owner_user_id=mem.user_id.id,
            generated_by='migration',
        )
        count += 1
    if count:
        _logger.info('OKF migration: %s personal memories → concepts', count)


def _migrate_ai_memory(env):
    """6.3: ai.memory med quest_id → coworker-scope (persistenta delar)."""
    Memory = env['ai.memory']
    Concept = env['ai.okf.concept']
    memories = Memory.search([('quest_id', '!=', False)])
    count = 0
    for mem in memories:
        key = 'ai.memory,%s' % mem.id
        if Concept.search_count([('concept_key', '=', key),
                                 ('scope', '=', 'coworker')]):
            continue
        Concept._okf_upsert(
            artifact_type=mem.artifact_type_id or 'learning',
            concept_key=key,
            summary=mem.content or '',
            title=mem.name or key,
            source_ref=key,
            owner_coworker_id=mem.quest_id.id,
            generated_by='migration',
        )
        count += 1
    if count:
        _logger.info('OKF migration: %s ai.memory → coworker concepts', count)


def _migrate_categories(env):
    """6.4: ai.company.memory.category → ai.artifact.type (bevara group_ids)."""
    Category = env['ai.company.memory.category']
    if not Category:
        return
    cats = Category.search([])
    count = 0
    for cat in cats:
        existing = env['ai.artifact.type'].search(
            [('name', '=', cat.name or cat.category)], limit=1)
        if not existing:
            existing = env['ai.artifact.type'].create({
                'name': cat.name or cat.category,
                'kind': 'knowledge',
                'bridge_module': 'ai_agent_core',
                'group_ids': [(6, 0, cat.group_ids.ids)] if hasattr(
                    cat, 'group_ids') else [(6, 0, [])],
            })
            count += 1
    if count:
        _logger.info('OKF migration: %s categories → artifact types', count)


def _archive_legacy(env):
    """6.5: gamla modeller behålls som read-only (inga nya skrivningar)."""
    # Sätt config-flagga som gör legacy-indexerare inaktiva
    icp = env['ir.config_parameter'].sudo()
    icp.set_param('okf.legacy_readonly', 'True')
    _logger.info('OKF migration: legacy-modeller markerade read-only')


def migrate(cr, version):
    from odoo.api import Environment, SUPERUSER_ID
    env = Environment(cr, SUPERUSER_ID, {})
    _logger.info("Running migration 1.13: OKF tre-scope-migrering")
    try:
        _migrate_company_memory(env)
    except Exception as e:
        _logger.warning('OKF company memory migration failed: %s', e)
    try:
        _migrate_personal_memory(env)
    except Exception as e:
        _logger.warning('OKF personal memory migration failed: %s', e)
    try:
        _migrate_ai_memory(env)
    except Exception as e:
        _logger.warning('OKF ai.memory migration failed: %s', e)
    try:
        _migrate_categories(env)
    except Exception as e:
        _logger.warning('OKF category migration failed: %s', e)
    try:
        _archive_legacy(env)
    except Exception as e:
        _logger.warning('OKF legacy archive failed: %s', e)
    env.flush_all()
