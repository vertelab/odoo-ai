# -*- coding: utf-8 -*-
"""ai.artifact.type — registrerbar artefakt-taxonomi (OKF)."""

import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class AIArtifactType(models.Model):
    _name = 'ai.artifact.type'
    _description = 'Artifact Type (OKF)'
    _order = 'kind, name'

    name = fields.Char(
        string='Name',
        required=True,
        help='Kebab-case identifier, e.g. customer-profile, social-post')

    kind = fields.Selection([
        ('memory', 'Memory'),
        ('knowledge', 'Knowledge'),
    ], string='Kind', required=True, default='knowledge',
        help='memory = Odoo Mind dynamiskt minne (LLM-genererat, ADD-only, '
             'ägar-scope); knowledge = kunskapsinjektion (kuraterade '
             'artefakter, stale-policy från källa)')

    model_id = fields.Many2one(
        'ir.model', string='Source Model',
        help='Source model the artifact type is produced from')

    bridge_module = fields.Char(
        string='Bridge Module',
        help='The module that registered this type')

    okf_contract = fields.Json(
        string='OKF Contract',
        help='OKF metadata contract: sources, generated_by, stale_policy, '
             'stale_ttl_days, retention_purpose, retention_days')

    group_ids = fields.Many2many(
        'res.groups', string='Access Groups',
        help='Access control at artifact type level. Empty = public.')

    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', 'Artifact type name must be unique.'),
    ]

    @api.constrains('okf_contract')
    def _check_okf_contract(self):
        """okf_contract must be a dict (Json field enforces JSON already,
        but validate the structure and retention fields)."""
        allowed_retention = ('accounting', 'tax', 'crm_lead', 'employment',
                             'marketing', 'none')
        for rec in self:
            if rec.okf_contract is None:
                continue
            if not isinstance(rec.okf_contract, dict):
                raise ValidationError(
                    _('OKF Contract must be a JSON object.'))
            contract = rec.okf_contract
            if 'retention_purpose' in contract:
                rp = contract['retention_purpose']
                if rp not in allowed_retention:
                    raise ValidationError(
                        _('Invalid retention_purpose %r in OKF contract. '
                          'Allowed: %s') % (rp, ', '.join(allowed_retention)))
            if 'stale_policy' in contract:
                sp = contract['stale_policy']
                if sp not in ('source', 'fixed'):
                    raise ValidationError(
                        _('Invalid stale_policy %r. Allowed: source, fixed.')
                        % sp)

    @api.model_create_multi
    def create(self, vals_list):
        """Sätt bridge_module till den aktuella modulen om inte angivet."""
        for vals in vals_list:
            if not vals.get('bridge_module'):
                vals['bridge_module'] = self.env.context.get(
                    'module_name', 'ai_agent_core')
        return super().create(vals_list)

    def _get_stale_policy(self):
        """Returnera (stale_policy, stale_ttl_days) från kontraktet."""
        self.ensure_one()
        contract = self.okf_contract or {}
        return (contract.get('stale_policy', 'source'),
                contract.get('stale_ttl_days', 0))
