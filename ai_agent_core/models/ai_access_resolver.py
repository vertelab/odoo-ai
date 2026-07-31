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
