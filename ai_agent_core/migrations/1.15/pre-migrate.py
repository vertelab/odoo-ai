# -*- coding: utf-8 -*-
"""Pre-migration: clean up ALL stale data from old ai_agent module.

ai_agent_core replaces ai_agent. The old module may have left behind
tables, views, model data, menus, and actions that conflict with
ai_agent_core's own definitions. This script removes them ALL.
"""

import logging
_logger = logging.getLogger(__name__)

# Tables to drop (ai_agent_core will recreate them with correct schema)
STALE_TABLES = [
    'ai_quest_session_line',
    'ai_quest_session',
    'ai_quest',
    'ai_agent',
    'ai_agent_skill_rel',
    'ai_tool',
    'ai_agent_llm',
]

# Models that existed in ai_agent but NOT in ai_agent_core
# Their views, actions, menus must be removed
REMOVED_MODELS = [
    'ai.quest',
    'ai.quest.session',
    'ai.quest.session.line',
    'ai.memory',
    'ai.tool',
    'ai.agent.llm',
    'ai.canvas.capability',
    'ai.coworker.monthly_summary',
]


def migrate(cr, version):
    """Remove all traces of old ai_agent module."""

    # 1. Drop stale tables
    for table in STALE_TABLES:
        try:
            cr.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s AND table_schema = 'public'
            """, [table])
            if cr.fetchone():
                cr.execute('DROP TABLE IF EXISTS "%s" CASCADE' % table)
                _logger.info('Pre-migrate: dropped stale table %s', table)
        except Exception as e:
            _logger.warning('Pre-migrate: could not drop table %s: %s', table, e)
            cr.rollback()

    # 2. Remove all ir_model_data records from ai_agent module
    try:
        cr.execute("DELETE FROM ir_model_data WHERE module = 'ai_agent'")
        deleted = cr.rowcount
        if deleted:
            _logger.info('Pre-migrate: deleted %s ir_model_data records from ai_agent', deleted)
    except Exception as e:
        _logger.warning('Pre-migrate: ir_model_data cleanup failed: %s', e)
        cr.rollback()

    # 3. Remove views for models that no longer exist
    if REMOVED_MODELS:
        try:
            cr.execute(
                "DELETE FROM ir_ui_view WHERE model = ANY(%s)",
                [REMOVED_MODELS]
            )
            deleted = cr.rowcount
            if deleted:
                _logger.info('Pre-migrate: deleted %s ir_ui_view records for removed models', deleted)
        except Exception as e:
            _logger.warning('Pre-migrate: ir_ui_view cleanup failed: %s', e)
            cr.rollback()

    # 4. Remove actions for removed models
    if REMOVED_MODELS:
        try:
            cr.execute(
                "DELETE FROM ir_act_window WHERE res_model = ANY(%s)",
                [REMOVED_MODELS]
            )
            cr.execute(
                "DELETE FROM ir_act_server WHERE model_id IN "
                "(SELECT id FROM ir_model WHERE model = ANY(%s))",
                [REMOVED_MODELS]
            )
        except Exception as e:
            _logger.warning('Pre-migrate: action cleanup failed: %s', e)
            cr.rollback()

    # 5. Remove orphaned menu items (parent was from ai_agent)
    try:
        cr.execute("""
            DELETE FROM ir_ui_menu
            WHERE parent_id IN (
                SELECT id FROM ir_ui_menu
                WHERE name LIKE '%%AI%%'
                  AND id NOT IN (SELECT res_id FROM ir_model_data WHERE module = 'ai_agent_core')
            )
        """)
        cr.execute("""
            DELETE FROM ir_ui_menu
            WHERE name ILIKE '%%quest%%'
               OR name ILIKE '%%ai agent%%'
        """)
    except Exception as e:
        _logger.warning('Pre-migrate: menu cleanup failed: %s', e)
        cr.rollback()

    _logger.info('Pre-migrate: ai_agent cleanup complete')
