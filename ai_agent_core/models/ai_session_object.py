# -*- coding: utf-8 -*-
"""ai.coworker.session.object — vilka affärsobjekt en session rörde.

VARFÖR (session-object-lineage): `ai.session.object` i `ai_agent` är
oinstallerad i drift, saknar coworker-koppling och skrivs bara av
`ai_agent`-egna vägar — aldrig av core-loopen. Konsekvensen var att
`session_object_count` på `ai.coworker` räknade ett fält som inte fanns och
alltid visade 0.

Den här tabellen skrivs av DEN VÄG SOM UTFÖR OPERATIONEN (verktygslagret,
`core/tools.py`), inte av efterhandsanalys av sessionens text. En rad per
(session, modell, objekt, operation) — en upprepad skrivning av samma objekt
uppdaterar tidpunkten i stället för att skapa en ny rad (idempotens).
"""

from odoo import api, fields, models


class AICoworkerSessionObject(models.Model):
    _name = 'ai.coworker.session.object'
    _description = 'Session Object Lineage'
    _order = 'datetime desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session', required=True,
        ondelete='cascade', index=True,
        help='Sessionen som utförde operationen. Lineage kan inte skapas '
             'utan en existerande session, och raderna följer sessionens '
             'livscykel (cascade).')
    coworker_id = fields.Many2one(
        'ai.coworker', string='AI Medarbetare',
        related='session_id.coworker_id', store=True, index=True,
        help='Medarbetaren som äger sessionen — lagras på raden så att '
             'medarbetarens objekt kan listas utan att gå via sessionens '
             'rader.')
    res_model = fields.Char(
        'Modell', required=True, index=True,
        help='Tekniskt modellnamn för objektet, t.ex. project.task.')
    res_id = fields.Integer(
        'Objekt-id', required=True,
        help='Objektets id i `res_model`.')
    operation = fields.Selection([
        ('created', 'Skapad'),
        ('updated', 'Uppdaterad'),
        ('deleted', 'Raderad'),
    ], string='Operation', required=True, index=True,
        help='Vad sessionen gjorde med objektet. `created` är den fråga '
             'knappen Objects svarar på; `updated` och `deleted` lagras för '
             'spårbarhet.')
    datetime = fields.Datetime(
        'Tidpunkt', default=fields.Datetime.now, required=True,
        help='När operationen senast utfördes för detta objekt i denna '
             'session (uppdateras vid upprepad skrivning).')

    _sql_constraints = [
        ('session_object_unique',
         'UNIQUE(session_id, res_model, res_id, operation)',
         'Samma objekt och operation loggas en gång per session.'),
    ]

    @api.model
    def _log_operation(self, session_id, res_model, res_ids, operation):
        """Registrera en operation i lineage — idempotent.

        Anropas av verktygslagret EFTER att operationen lyckats. Misslyckade
        eller HITL-avvisade anrop når aldrig hit, så de lämnar ingen rad.

        Saknas sessionen i kontexten görs ingenting: en lineage-rad utan
        session vore en påhittad koppling (samma princip som HITL:s
        `_resolve_session`).
        """
        if not session_id or not res_model or not res_ids:
            return 0
        if operation not in dict(self._fields['operation'].selection):
            return 0
        session = self.env['ai.coworker.session'].browse(int(session_id))
        if not session.exists():
            return 0
        now = fields.Datetime.now()
        written = 0
        for res_id in res_ids:
            if not res_id:
                continue
            existing = self.search([
                ('session_id', '=', session.id),
                ('res_model', '=', res_model),
                ('res_id', '=', int(res_id)),
                ('operation', '=', operation),
            ], limit=1)
            if existing:
                existing.write({'datetime': now})
            else:
                self.create({
                    'session_id': session.id,
                    'res_model': res_model,
                    'res_id': int(res_id),
                    'operation': operation,
                    'datetime': now,
                })
            written += 1
        return written
