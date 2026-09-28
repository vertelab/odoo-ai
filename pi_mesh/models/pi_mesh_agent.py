# -*- coding: utf-8 -*-
"""
pi.mesh.agent — en Pi-agent som lever på meshen.

Detta är människans överblick. NATS vet vilka som lyssnar just nu, men
bara i nuet: stänger agenten sin terminal finns den inte längre. Odoo
minns den, och kan svara på "vem arbetade i den här filen i går?".

Statusen sätts av en cron, inte av agenten själv. En agent som kraschar
kan inte meddela att den är död — frånvaron av heartbeat ÄR dödsbudet.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# En agent som inte hörts av på så länge antas vara borta. Tre
# heartbeat-intervall (30 s) är för kort — en tillfällig nätverkshake
# skulle fladdra statusen. 90 s ger två missade pulsslag innan vi
# drar slutsatsen.
OFFLINE_AFTER_SECONDS = 90


class PiMeshAgent(models.Model):
    _name = 'pi.mesh.agent'
    _description = 'Pi-agent på meshen'
    _order = 'status, hostname, name'
    _rec_name = 'name'

    name = fields.Char(
        'Agent-ID', required=True, index=True,
        help='Format: användare@värd, t.ex. waland@gw0. Samma användare på '
             'två maskiner är två agenter.',
    )
    hostname = fields.Char('Värd', index=True)
    role = fields.Selection(
        [('gateway', 'Gateway'),
         ('worker', 'Worker'),
         ('hub', 'Nav'),
         ('other', 'Övrig')],
        'Roll', default='worker',
    )
    status = fields.Selection(
        [('online', 'Online'),
         ('idle', 'Ledig'),
         ('working', 'Arbetar'),
         ('offline', 'Offline')],
        'Status', default='offline', index=True,
        help='Sätts av heartbeat-cronen. En agent som tystnar blir offline '
             'utan manuell åtgärd.',
    )
    last_seen = fields.Datetime('Senast hörd', index=True)
    first_seen = fields.Datetime('Först sedd', default=fields.Datetime.now)
    pid = fields.Integer('PID')

    # Vad agenten gör just nu.
    task = fields.Char('Pågående uppgift')
    claims = fields.Text(
        'Anspråk',
        help='Filer agenten rört, en per rad. Sätts automatiskt av '
             'tool_call-hooken i nats.ts — inte av agentens minne.',
    )
    locks = fields.Text('Lås', help='Resurser agenten håller, en per rad.')
    skills = fields.Text('Skills', help='Aktiva skills, en per rad.')

    session_count = fields.Integer('Sessioner', compute='_compute_counts')
    message_count = fields.Integer('Meddelanden', compute='_compute_counts')
    lock_count = fields.Integer(
        'Aktiva lås', compute='_compute_counts', store=True,
        help='Lagras så att det går att filtrera på — en agent som håller '
             'lås är den man letar efter när något blockerar.',
    )

    claim_list = fields.Char('Anspråk (kort)', compute='_compute_claim_list')
    color = fields.Integer('Färg', compute='_compute_color')

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Agent-ID måste vara unikt.'),
    ]

    # ── Beräkningar ────────────────────────────────────────────────

    @api.depends('claims', 'locks', 'name')
    def _compute_counts(self):
        Msg = self.env['pi.mesh.message']
        Lock = self.env['pi.mesh.lock']
        for agent in self:
            agent.session_count = 0
            agent.message_count = Msg.search_count([
                '|', ('agent_id', '=', agent.name),
                ('recipient', '=', agent.name),
            ])
            agent.lock_count = Lock.search_count([
                ('agent_id', '=', agent.name),
                ('state', '=', 'held'),
            ])

    def _recompute_lock_count(self):
        """Räkna om lock_count för de agenter som berörs av en låsändring.

        VARFÖR en egen väg: `lock_count` är `store=True` för att gå att
        filtrera på, men den beror på en ANNAN modell (pi.mesh.lock).
        Odoo:s `@api.depends` ser bara fält på samma post, så utan detta
        skulle räknaren aldrig uppdateras när ett lås tas eller släpps.
        Anropas från pi.mesh.lock efter create/write/unlink.
        """
        names = {a for a in self.mapped('name') if a}
        if not names:
            return
        agents = self.search([('name', 'in', list(names))])
        if agents:
            agents._compute_counts()

    @api.depends('claims')
    def _compute_claim_list(self):
        for agent in self:
            lines = [l.strip() for l in (agent.claims or '').splitlines() if l.strip()]
            agent.claim_list = ', '.join(lines[:3]) + (
                f' (+{len(lines) - 3})' if len(lines) > 3 else ''
            )

    @api.depends('status')
    def _compute_color(self):
        palette = {
            'online': 4,    # grön
            'working': 10,  # blå
            'idle': 3,      # gul
            'offline': 1,   # grå
        }
        for agent in self:
            agent.color = palette.get(agent.status, 0)

    # ── Heartbeat ──────────────────────────────────────────────────

    @api.model
    def register_heartbeat(self, agent_id, hostname=None, pid=None,
                           status=None, task=None, claims=None, locks=None,
                           skills=None):
        """Ta emot ett pulsslag. Skapar agenten första gången.

        Idempotent: samma agent-id uppdaterar samma post. Anropas från
        webhook-kontrollern, aldrig från cron.
        """
        if not agent_id:
            return self.browse()

        agent = self.search([('name', '=', agent_id)], limit=1)
        vals = {
            'last_seen': fields.Datetime.now(),
            'hostname': hostname or (agent.hostname if agent else False),
            'pid': pid or (agent.pid if agent else False),
        }
        if status:
            vals['status'] = status
        elif agent and agent.status == 'offline':
            # En agent som hörs av igen är tillbaka — men vi gissar inte
            # att den arbetar; den får säga det själv.
            vals['status'] = 'online'
        if task is not None:
            vals['task'] = task
        if claims is not None:
            vals['claims'] = '\n'.join(claims) if isinstance(claims, list) else claims
        if locks is not None:
            vals['locks'] = '\n'.join(locks) if isinstance(locks, list) else locks
        if skills is not None:
            vals['skills'] = '\n'.join(skills) if isinstance(skills, list) else skills

        if agent:
            agent.write(vals)
        else:
            vals['name'] = agent_id
            vals.setdefault('status', 'online')
            agent = self.create(vals)
            _logger.info('pi_mesh: ny agent registrerad: %s', agent_id)
        return agent

    @api.model
    def _cron_mark_offline(self):
        """Flytta tystnade agenter till offline.

        VARFÖR en cron och inte agenten själv: en kraschad agent kan inte
        rapportera sin egen död. Frånvaron av heartbeat är det enda
        tillförlitliga dödsbudet.
        """
        cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), seconds=OFFLINE_AFTER_SECONDS,
        )
        stale = self.search([
            ('status', '!=', 'offline'),
            '|', ('last_seen', '=', False), ('last_seen', '<', cutoff),
        ])
        if stale:
            stale.write({'status': 'offline', 'task': False})
            _logger.info('pi_mesh: %d agent(er) tystnade → offline', len(stale))
            # Lås som dessa agenter höll är nu döda. Räknas om så att
            # lås-vyns "Döda"-filter visar sanningen.
            self.env['pi.mesh.lock'].search([
                ('agent_id', 'in', stale.mapped('name')),
                ('state', '=', 'held'),
            ])._recompute_is_dead()
        return len(stale)

    # ── Åtgärder ───────────────────────────────────────────────────

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Meddelanden — {self.name}',
            'res_model': 'pi.mesh.message',
            'view_mode': 'list,form',
            'domain': ['|', ('agent_id', '=', self.name),
                       ('recipient', '=', self.name)],
            'context': {'default_agent_id': self.name},
        }

    def action_view_locks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Lås — {self.name}',
            'res_model': 'pi.mesh.lock',
            'view_mode': 'list,form',
            'domain': [('agent_id', '=', self.name)],
            'context': {'default_agent_id': self.name},
        }
