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

from odoo import _, api, fields, models


class AICoworkerSessionObject(models.Model):
    _name = 'ai.coworker.session.object'
    _description = 'Session Object Lineage'
    _order = 'datetime desc, id desc'

    session_id = fields.Many2one(
        'ai.coworker.session', string='Session', required=True,
        ondelete='cascade', index=True,
        help='The session that performed the operation. Lineage cannot be created without an existing session, and the rows follow the session lifecycle (cascade).')
    coworker_id = fields.Many2one(
        'ai.coworker', string='AI Medarbetare',
        related='session_id.coworker_id', store=True, index=True,
        help='The coworker that owns the session — stored on the row so the coworker objects can be listed without going through the session lines.')
    res_model = fields.Char(
        'Modell', required=True, index=True,
        help='Technical model name for the object, e.g. project.task.')
    res_id = fields.Integer(
        'Objekt-id', required=True,
        help='Objektets id i `res_model`.')
    operation = fields.Selection([
        ('created', 'Skapad'),
        ('updated', 'Uppdaterad'),
        ('deleted', 'Raderad'),
    ], string='Operation', required=True, index=True,
        help='What the session did with the object. `created` is the question the Objects button answers; `updated` and `deleted` are stored for traceability.')
    datetime = fields.Datetime(
        'Tidpunkt', default=fields.Datetime.now, required=True,
        help='When the operation was last performed for this object in this session (updated on repeated writes).')

    _sql_constraints = [
        ('session_object_unique',
         'UNIQUE(session_id, res_model, res_id, operation)',
         'The same object and operation are logged once per session.'),
    ]

    # ── Navigering till målet (session-object-navigation) ──────────────
    #
    # Lineage är polymorf: `res_model`/`res_id` kan peka på vilken modell som
    # helst, och det finns ingen FK. Målet slås därför upp först när
    # åtgärden används — aldrig vid listladdning (kravet "Navigeringen är
    # billig vid listladdning"). Uppslaget går via målmodellens EGET
    # `get_formview_action()`, inte via en handbyggd URL, så att modellens
    # vy, meny och behörighetsregler gäller (Beslut 1 och 6).

    def action_open_target(self):
        """Öppna det affärsobjekt raden pekar på.

        Returnerar målmodellens egen formuläråtgärd, eller en notis som
        förklarar varför objektet inte kan öppnas (Beslut 3). Aldrig `False`:
        en åtgärd som returnerar `False` gör ingenting alls och lämnar
        användaren utan förklaring.
        """
        self.ensure_one()
        failure = self._target_unavailable_reason()
        if failure:
            return self._target_unavailable_notification(failure)
        target = self.env[self.res_model].browse(int(self.res_id))
        return target.get_formview_action()

    def _target_unavailable_reason(self):
        """Varför målet inte kan öppnas — eller `None` om det går.

        Tre fall, i den ordning de kan avgöras billigast:
          1. modellen finns inte i registret (avinstallerad)
          2. modellen finns men användaren saknar läsrätt
          3. posten finns inte längre (raderad sedan raden skrevs)

        Prövningen sker med användarens EGET env — aldrig `sudo()`.
        Lineage-läsning får inte eskalera behörigheter, och ett `sudo()` här
        skulle göra `has_access()` till en tyst ja-stämpel.
        """
        if not self.res_model or self.res_model not in self.env:
            return 'missing_model'
        if not self.res_id:
            return 'missing_record'
        target_model = self.env[self.res_model]
        if not target_model.has_access('read'):
            return 'no_access'
        if not target_model.browse(int(self.res_id)).exists():
            return 'missing_record'
        return None

    def _target_unavailable_notification(self, reason):
        """Notis som säger VARFÖR objektet inte öppnas.

        Ett raderat objekt är normal historik, inte ett systemfel — därför
        en notis i stället för `UserError` (Beslut 3). Raden ligger kvar i
        listan; bara navigeringen är stängd.
        """
        messages = {
            'missing_model': _(
                'The model %(model)s is not available in this database. The object cannot be opened.',
                model=self.res_model or '-'),
            'missing_record': _(
                'The object no longer exists. It may have been deleted after the session logged it.'),
            'no_access': _(
                'You lack permission to read %(model)s. The object cannot be opened.', model=self.res_model),
        }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('The object cannot be opened'),
                'message': messages.get(
                    reason, _('The object cannot be opened.')),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

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
