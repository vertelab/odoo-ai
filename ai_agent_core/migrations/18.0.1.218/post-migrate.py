"""coworker-dispatch-owner: ge aktiva coworkers en ägare.

Bakgrund (mätt 2026-09-18): 29 av 29 aktiva coworkers saknade
`chat_user_id` och 0 bot-users existerade. Bron från session till
personligt minne kunde därför inte skriva något.

Reparationen körs via ORM:en efter att kolumnen och fältet finns.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    repaired = env['ai.coworker']._repair_missing_chat_users()
    _logger.info(
        'coworker-dispatch-owner: %d coworkers fick en ägare', repaired)
