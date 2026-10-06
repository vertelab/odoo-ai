# -*- coding: utf-8 -*-
"""ai.access.resolver — registrerbar access-resolver (OKF).

Odoos access-modell (ir.access ∩ ir.rule) är enda sanning. Resolvern
registrerar bara delningsmönster som ir.access/ir.rule inte uttrycker
(followers, owner-only). Resolver-domäner är AND-filter — aldrig breddare.
"""

import logging
import re

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AIAccessResolver(models.Model):
    _name = 'ai.access.resolver'
    _description = 'OKF Access Resolver'
    _order = 'model_id'

    model_id = fields.Many2one(
        'ir.model', string='Source Model', required=True, ondelete='cascade')
    source_kind = fields.Selection([
        ('odoo_record', 'Odoo Record'),
        ('external_url', 'External URL'),
        ('attachment', 'Attachment'),
    ], string='Source Kind', default='odoo_record',
        help='odoo_record = access via Odoo ORM; external_url = ingen Odoo-'
             'post (access via artefakttypens group_ids); attachment = '
             'ir.attachment-kontext (res_model/res_id)')
    follower_domain = fields.Char(
        string='Follower Domain',
        help='Domain-template med {partner_id}-placeholder, t.ex. '
             "[('message_follower_ids','in',['{partner_id}'])] — använder "
             'faktiska followers (message_follower_ids), INTE alla '
             'chatter-partners')
    owner_domain = fields.Char(
        string='Owner Domain',
        help='Domain-template med {user_id}-placeholder, t.ex. '
             "[('user_id','=', '{user_id}')]")
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('model_uniq', 'UNIQUE(model_id)',
         'One resolver per source model.'),
    ]

    def _resolve_domain(self, domain_template, user):
        """Ersätt placeholders ({partner_id}, {user_id}) i en domain-template."""
        self.ensure_one()
        if not domain_template:
            return None
        partner_id = user.partner_id.id if user else 0
        user_id = user.id if user else 0
        try:
            # Byt placeholders i strängen, sedan safe_eval till domain.
            # repr() ger Python-literal: 7 → 7 (int), 'x' → 'x' (str).
            rendered = domain_template.replace(
                '{partner_id}', repr(partner_id)).replace(
                '{user_id}', repr(user_id))
            from odoo.tools.safe_eval import safe_eval
            domain = safe_eval(rendered)
            if not isinstance(domain, (list, tuple)):
                raise ValueError('domain must be a list/tuple')
            return list(domain)
        except Exception as e:
            _logger.warning('Could not resolve resolver domain %r: %s',
                            domain_template, e)
            return None

    def _get_domains(self, user):
        """Returnera (follower_domain, owner_domain) som domäner för user."""
        self.ensure_one()
        follower = self._resolve_domain(self.follower_domain, user)
        owner = self._resolve_domain(self.owner_domain, user)
        return follower, owner

    # ════════════════════════════════════════════
    # Klassning per källmodell (okf-owner-and-access-scoping D2)
    # ════════════════════════════════════════════
    #
    # VARFÖR EN KLASSNING OCH INTE EN ID-LISTA: volymen. I `ledningssystem`
    # (mätt 2026-10-06) har `project.task` ~50 000 rader och `res.partner`
    # ~3 300. En `IN`-lista över alla synliga id:n för en projektledare som
    # ser ALLT blir 50 000 element — det spränger plan-cachen och är ingen
    # lösning. Klassningen ger i stället ett av tre svar, och bara PARTIELL
    # behöver en id-lista (som då per definition är mindre än modellens
    # total).

    OPEN = 'open'          # användaren ser allt — inget villkor behövs
    CLOSED = 'closed'      # användaren ser inget — modellen utesluts
    PARTIAL = 'partial'    # användaren ser en delmängd — id-lista

    @api.model
    def _classify_source_model(self, model_name, user):
        """Klassa en källmodell för en användare: OPEN/CLOSED/PARTIAL.

        Två `search_count` — ett aggregat, ingen radhämtning:

            total   = Model.sudo().search_count([])
            visible = Model.with_user(uid).search_count([])

            visible == total  -> OPEN
            visible == 0      -> CLOSED
            annars            -> PARTIAL

        Returnerar `(kind, ids)` där `ids` bara är satt för PARTIAL.

        Fail-closed: en modell som inte finns i `self.env`, eller där
        prövningen kastar, klassas som CLOSED — inte OPEN. Ett saknat svar
        får aldrig tolkas som "synligt".
        """
        Model = self.env.get(model_name)
        if Model is None:
            return self.CLOSED, None
        uid = user.id if user else self.env.uid
        try:
            total = Model.sudo().search_count([])
        except Exception as e:  # noqa: BLE001
            _logger.warning(
                'OKF access: kunde inte räkna %s — klassas CLOSED: %s',
                model_name, e)
            return self.CLOSED, None
        if total == 0:
            # Ingen post alls — inget att läsa, men inte heller något att
            # utesluta. OPEN ger inget villkor och är billigast.
            return self.OPEN, None
        try:
            visible_count = Model.with_user(uid).search_count([])
        except Exception as e:  # noqa: BLE001
            _logger.warning(
                'OKF access: kunde inte pröva %s för uid=%s — klassas '
                'CLOSED: %s', model_name, uid, e)
            return self.CLOSED, None
        if visible_count >= total:
            return self.OPEN, None
        if visible_count == 0:
            return self.CLOSED, None
        try:
            visible_ids = Model.with_user(uid).search([]).ids
        except Exception as e:  # noqa: BLE001
            _logger.warning(
                'OKF access: kunde inte hämta synliga id:n för %s — '
                'klassas CLOSED: %s', model_name, e)
            return self.CLOSED, None
        return self.PARTIAL, visible_ids

    @api.model
    def _source_ref_condition(self, user, model_names):
        """Bygg ett SQL-villkor mot `source_ref` ur klassningen (D3).

        Returnerar `(sql_fragment, params)` där fragmentet är tomt när inget
        villkor behövs (alla modeller OPEN), eller `None` när INGET koncept
        kan vara läsbart (alla modeller CLOSED).

        Formen:

            (source_ref LIKE 'project.task,%'      -- OPEN
             OR source_ref IN ('res.partner,10')   -- PARTIAL
             OR source_ref IS NULL)                -- hanteras av D4-nätet

        CLOSED-modeller nämns inte alls.

        `source_ref IS NULL` ingår alltid: ett koncept utan källhänvisning
        kan inte prövas här och faller till D4-nätet (fail-closed), inte
        till "synligt".
        """
        or_parts = []
        params = {}
        open_models = []
        partial_models = []
        closed_models = []
        for name in model_names:
            kind, ids = self._classify_source_model(name, user)
            if kind == self.OPEN:
                open_models.append(name)
            elif kind == self.CLOSED:
                closed_models.append(name)
            else:
                partial_models.append((name, ids or []))

        for name in open_models:
            key = 'open_%s' % re.sub(r'\W', '_', name)
            or_parts.append('source_ref LIKE %(' + key + ')s')
            params[key] = '%s,%%' % name

        idx = 0
        for name, ids in partial_models:
            if not ids:
                continue
            refs = ['%s,%s' % (name, rid) for rid in ids]
            key = 'part_%s' % idx
            or_parts.append('source_ref = ANY(%(' + key + ')s)')
            params[key] = refs
            idx += 1

        _logger.info(
            'OKF access: klassning open=%s partial=%s closed=%s',
            open_models,
            [(n, len(i)) for n, i in partial_models],
            closed_models)

        # Inget koncept kan vara läsbart: alla källmodeller är stängda.
        #
        # FYND (2026-10-06): detta får bara gälla när vi FAKTISKT klassade
        # modeller och alla var stängda. En TOM modellista (inga koncept har
        # `source_ref` alls — t.ex. i enhetstester som skapar koncept via rå
        # SQL) är inte "allt stängt": det finns inget att begränsa, och
        # koncepten faller till D4-nätet. Att tolka tomt som stängt gjorde
        # att hela sökningen blev tom — 10 tester föll.
        if not model_names:
            return '', params
        if not or_parts:
            return None, params

        # `source_ref IS NULL` alltid med: oprövade koncept faller till D4.
        or_parts.append('source_ref IS NULL')
        return '(' + ' OR '.join(or_parts) + ')', params
