# -*- coding: utf-8 -*-
"""Access-cache-invalidering: ir.rule, followers, groups.

ir.rules ändras sällan → materialiserad cache träffsäker. Dessa hooks
ogiltigförklarar cachen (ökar okf.access_cache_version) när access-
relevant data ändras.
"""

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class IrRule(models.Model):
    _inherit = 'ir.rule'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['ai.okf.concept']._invalidate_access_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        self.env['ai.okf.concept']._invalidate_access_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env['ai.okf.concept']._invalidate_access_cache()
        return res


class MailFollowers(models.Model):
    _inherit = 'mail.followers'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['ai.okf.concept']._invalidate_access_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        self.env['ai.okf.concept']._invalidate_access_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env['ai.okf.concept']._invalidate_access_cache()
        return res


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        res = super().write(vals)
        if 'users' in vals or 'implied_ids' in vals:
            self.env['ai.okf.concept']._invalidate_access_cache()
        return res
