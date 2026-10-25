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

    def _load_records_write(self, values):
        """Bumpa cache-versionen FÖRE skrivningen, inte efter.

        `res.groups.write()` → `ir.model.access.call_cache_clearing_methods()`
        → `invalidate_all()` → `flush_all()` skickar iväg ALLA pending writes
        i transaktionen. Om vår bump ligger efter `super().write()` hamnar
        `implied_ids` (res_groups_implied_rel) och bumpen
        (ir_config_parameter) i samma flush, och två samtidiga
        moduluppgraderingar tar låsen i olika ordning:

            Process A: res_groups_implied_rel → ir_config_parameter
            Process B: ir_config_parameter  → res_groups_implied_rel
            → psycopg2.errors.DeadlockDetected

        Mätt i drift 2026-10-25: `button_immediate_upgrade` på ai_agent_core
        dog i `security/ai_groups.xml` med deadlock på ir_module_module,
        medan en annan uppgradering höll transaktionen.

        Genom att bumpa först ärver den yttre transaktionen låset på
        ir_config_parameter innan gruppraderna skrivs, så alla transaktioner
        tar låsen i samma ordning. Bumpen ligger i en egen savepoint och kan
        aldrig fälla skrivningen — samma garanti som `write()`-hooken hade.
        """
        if 'implied_ids' in values or 'users' in values:
            self.env['ai.okf.concept']._invalidate_access_cache()
        return super()._load_records_write(values)
