"""Räkna om is_dead för alla lås.

VARFÖR: `is_dead` är ett lagrat fält som beror på `state`, men det
räknades bara om när state ändrades via cronen — och cronen satte
tidigare is_dead=False för ALLA icke-held lås, inklusive 'expired'.
Resultatet var att fältet var False i drift för precis de lås det fanns
för att hitta.

Framtida lås är fixade i _compute_is_dead/_cron_expire_locks. Den här
migrationen rättar de rader som redan finns — annars står de kvar med
fel värde för alltid, eftersom ingenting rör dem igen.

Idempotent: att köra den två gånger ger samma resultat.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    locks = env['pi.mesh.lock'].search([])
    if not locks:
        _logger.info('pi_mesh: inga lås att räkna om')
        return
    locks._compute_is_dead()
    dead = locks.filtered('is_dead')
    _logger.info(
        'pi_mesh: räknade om is_dead för %d lås, %d döda',
        len(locks), len(dead),
    )
