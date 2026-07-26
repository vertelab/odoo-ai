# -*- coding: utf-8 -*-
"""Migration: Create ai.quest.init_type records for existing quests.

Reads existing init_type + model_id from ai.quest and creates
corresponding ai.quest.init_type records.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Create init_type records for all existing quests."""
    env = __import__('odoo').api.Environment(cr, 1, {})

    # Map old init_type to new init_type (same values)
    quests = env['ai.quest'].search([])
    created = 0

    for quest in quests:
        old_type = quest.init_type or 'manual'

        # Check if already has init types (re-run safe)
        if quest.init_type_ids:
            continue

        vals = {
            'quest_id': quest.id,
            'init_type': old_type,
            'active': quest.status == 'active',
        }

        # Copy type-specific fields from quest to init_type
        if old_type == 'web_ui':
            vals['show_in_chat'] = quest.show_in_chat
        elif old_type == 'chat':
            vals['chat_user_id'] = quest.chat_user_id.id if quest.chat_user_id else False
            vals['use_chat_history'] = quest.use_chat_history
            vals['chat_history_limit'] = quest.chat_history_limit
            vals['allow_trigger_words'] = quest.allow_trigger_words
            vals['chat_trigger_words'] = quest.chat_trigger_words
        elif old_type == 'channel':
            vals['channel_id'] = quest.channel_id.id if quest.channel_id else False
            vals['allow_trigger_words'] = quest.allow_trigger_words
            vals['chat_trigger_words'] = quest.chat_trigger_words
        elif old_type == 'mail':
            vals['alias_name'] = quest.alias_name if hasattr(quest, 'alias_name') else ''
            vals['alias_contact'] = getattr(quest, 'alias_contact', 'everyone')
        elif old_type == 'cron':
            vals['cron_id'] = quest.cron_id.id if quest.cron_id else False
            vals['filter_domain'] = quest.filter_domain
        elif old_type == 'server_action':
            vals['server_action_id'] = quest.server_action_id.id if quest.server_action_id else False
        elif old_type == 'openai_api':
            vals['show_in_chat'] = False  # Not a chat-facing type by default

        env['ai.quest.init_type'].create(vals)
        created += 1

    _logger.info('Migration: Created %d ai.quest.init_type records', created)
