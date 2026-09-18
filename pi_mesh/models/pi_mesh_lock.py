# -*- coding: utf-8 -*-
"""
pi.mesh.lock — exklusiva lås på resurser.

Låset i nats.ts dör av sig själv efter TTL. Men TTL:en lever bara i
agentens minne: kraschar agenten finns ingen kvar som vet att låset
fanns. Här finns det kvar, med en utgångstid, och en cron kan städa
det — och därmed svara på frågan "varför nekades jag?" i efterhand.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class PiMeshLock(models.Model):
    _name = 'pi.mesh.lock'
    _description = 'Lås på en mesh-resurs'
    _order = 'state, expires_at, resource'

    resource = fields.Char('Resurs', required=True, index=True)
    agent_id = fields.Char('Ägare', required=True, index=True)
    acquired_at = fields.Datetime(
        'Togs', default=fields.Datetime.now, required=True,
    )
    expires_at = fields.Datetime('Går ut', required=True, index=True)
    released_at = fields.Datetime('Släppt')
    state = fields.Selection(
        [('held', 'Hållen'),
         ('released', 'Släppt'),
         ('expired', 'Utgången')],
        'Tillstånd', default='held', index=True,
    )
    reason = fields.Char('Orsak', help='Varför låset släpptes, om känt.')
    ttl_seconds = fields.Integer('TTL (s)', default=300)

    held_for = fields.Float('Hållen i (min)', compute='_compute_held_for')
    is_dead = fields.Boolean(
        'Dött', compute='_compute_is_dead', store=True,
        help='Lagras så att det går att filtrera på — vyns hela poäng är '
             'att hitta de lås som blockerar utan att någon håller dem.',
    )

    @api.depends('acquired_at', 'expires_at', 'state')
    def _compute_held_for(self):
        now = fields.Datetime.now()
        for lock in self:
            if lock.acquired_at:
                end = lock.released_at or now
                lock.held_for = (end - lock.acquired_at).total_seconds() / 60.0
            else:
                lock.held_for = 0.0

    @api.depends('expires_at', 'state')
    def _compute_is_dead(self):
        """Ett lås vars ägare tystnat är dött även om TTL:en tickar.

        Detta är exakt buggen från Fas 3: ett lås som såg levande ut för
        att det fanns, men vars ägare var borta. Här gör vi den
        distinktionen synlig i stället för att dölja den.
        """
        now = fields.Datetime.now()
        Agent = self.env['pi.mesh.agent']
        # En sökning i stället för en per lås — annars blir en lista med
        # 50 lås 50 frågor.
        owner_status = {
            a.name: a.status for a in Agent.search([
                ('name', 'in', list({l.agent_id for l in self if l.agent_id})),
            ])
        }
        for lock in self:
            if lock.state != 'held':
                lock.is_dead = False
                continue
            expired = bool(lock.expires_at and lock.expires_at < now)
            owner_gone = owner_status.get(lock.agent_id) == 'offline'
            lock.is_dead = expired or owner_gone

    def _recompute_is_dead(self):
        """Räkna om is_dead för de lås en agent äger.

        VARFÖR: `is_dead` beror på agentens status, som ändras av
        heartbeat-cronen — inte av låset. Utan denna väg skulle ett lås
        fortsätta se levande ut efter att ägaren tystnat.
        """
        names = [n for n in set(self.mapped('agent_id')) if n]
        if not names:
            return
        locks = self.search([('agent_id', 'in', names)])
        if locks:
            locks._compute_is_dead()

    def create(self, vals_list):
        locks = super().create(vals_list)
        locks._sync_agent_lock_count()
        return locks

    def write(self, vals):
        res = super().write(vals)
        # Bara när något som påverkar räknaren ändrats — annars skriver
        # varje expires_at-förnyelse om agentposten i onödan.
        if {'state', 'agent_id', 'resource'} & set(vals):
            self._sync_agent_lock_count()
        return res

    def unlink(self):
        agents = self.mapped('agent_id')
        res = super().unlink()
        self.env['pi.mesh.agent']._recompute_lock_count()
        return res

    def _sync_agent_lock_count(self):
        """Låt agentposten räkna om sina lås.

        `lock_count` är store=True för att gå att filtrera på, men den
        beror på DENNA modell. Utan denna koppling skulle en agent som
        tar ett lås aldrig visa det i listan.
        """
        Agent = self.env['pi.mesh.agent']
        names = [n for n in set(self.mapped('agent_id')) if n]
        if names:
            Agent.search([('name', 'in', names)])._compute_counts()

    # ── Städning ───────────────────────────────────────────────────

    @api.model
    def _cron_expire_locks(self):
        """Släpp lås vars TTL passerat.

        Specen: "WHEN ett lås har passerat sin timeout THEN släpps det av
        en ir.cron och försvinner ur lås-vyn."

        Vi raderar inte — vi markerar som utgången. Ett lås som försvann
        spårlöst kan inte förklara varför en agent nekades i går.
        """
        now = fields.Datetime.now()
        expired = self.search([
            ('state', '=', 'held'),
            ('expires_at', '<', now),
        ])
        count = len(expired)
        if count:
            expired.write({
                'state': 'expired',
                'released_at': now,
                'reason': 'TTL löpte ut utan förnyelse',
            })
            _logger.info('pi_mesh: %d lås utgick', count)
        return count

    # ── Registrering (från webhook) ────────────────────────────────

    @api.model
    def register_lock(self, resource, agent_id, ttl_seconds=300):
        """Skapa eller förnya ett lås. Idempotent per (resurs, ägare)."""
        if not resource or not agent_id:
            return self.browse()
        now = fields.Datetime.now()
        expires = fields.Datetime.add(now, seconds=int(ttl_seconds))

        existing = self.search([
            ('resource', '=', resource),
            ('agent_id', '=', agent_id),
            ('state', '=', 'held'),
        ], limit=1)
        if existing:
            existing.write({'expires_at': expires, 'ttl_seconds': int(ttl_seconds)})
            return existing

        # Någon annan håller den? Stäng deras lås först — annars ligger
        # två levande lås på samma resurs, vilket är precis den
        # oklarhet modulen finns för att undvika.
        others = self.search([
            ('resource', '=', resource),
            ('state', '=', 'held'),
            ('agent_id', '!=', agent_id),
        ])
        if others:
            others.write({
                'state': 'released',
                'released_at': now,
                'reason': f'övertagen av {agent_id}',
            })
            _logger.info(
                'pi_mesh: lås på %s övertogs av %s (från %s)',
                resource, agent_id, ', '.join(others.mapped('agent_id')),
            )

        return self.create({
            'resource': resource,
            'agent_id': agent_id,
            'acquired_at': now,
            'expires_at': expires,
            'ttl_seconds': int(ttl_seconds),
            'state': 'held',
        })

    @api.model
    def release_lock(self, resource, agent_id):
        """Släpp ett lås. Returnerar antalet släppta."""
        if not resource or not agent_id:
            return 0
        locks = self.search([
            ('resource', '=', resource),
            ('agent_id', '=', agent_id),
            ('state', '=', 'held'),
        ])
        if locks:
            locks.write({
                'state': 'released',
                'released_at': fields.Datetime.now(),
                'reason': 'släppt av ägaren',
            })
        return len(locks)
