# -*- coding: utf-8 -*-
"""Registrera org_chart som egen vytyp (den installerade Odoo-web saknar
hierarchy-vytypen, så AI Orkestrering får en egen org-chart istället)."""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    type = fields.Selection(selection_add=[('org_chart', 'Org Chart')])

    def _postprocess_tag_org_chart(self, node, name_manager, node_info):
        pass

    def _get_view_info(self):
        return {'org_chart': {'icon': 'fa fa-sitemap'}} | super()._get_view_info()

    # ── sv_SE-vyöversättningsvakt (view-translation-guard) ────────────
    # Vertels i18n_extra/sv.po (crm) översatte FÄLTNAMN i invisible-uttryck:
    #   duplicate_lead_count → duplicate_kundämne_count  (fält finns inte)
    #   use_leads            → use_kundämnen              (fält finns inte)
    # Odoo 18:s expression-parser omsluter uttrycket med bool(...) och kan
    # inte parsa icke-existerande fält/icke-ASCII → OwlError "Invalid
    # expression" när sv_SE-formulär renderas. Denna metod ersätter
    # idempotent tillbaka till verkliga fältnamn i ALLA vyer — oavsett om
    # po-filen fortfarande är felaktig (checkmodule --load-language=sv
    # applicerar po-filen vid varje uppdatering).
    #
    # Körs av cron cron_view_translation_guard (se data/cron_view_guard.xml)
    # och vid moduluppdatering (_ensure_init_resources).
    _SWEDISH_VIEW_FIELD_FIXES = {
        'duplicate_kundämne_count': 'duplicate_lead_count',
        'use_kundämnen': 'use_leads',
    }

    @api.model
    def _fix_swedish_view_arch(self):
        """Korrigera översatta fältnamn i sv_SE-vyarcher (idempotent).

        Returns:
            int: antal vyer som korrigerats.
        """
        fixed = 0
        for bad, good in self._SWEDISH_VIEW_FIELD_FIXES.items():
            self.env.cr.execute("""
                UPDATE ir_ui_view
                SET arch_db = jsonb_set(
                        arch_db, '{sv_SE}',
                        to_jsonb(replace(arch_db->>'sv_SE', %s, %s)))
                WHERE arch_db->>'sv_SE' LIKE '%%' || %s || '%%'
            """, (bad, good, bad))
            fixed += self.env.cr.rowcount
        if fixed:
            self.clear_caches()
            _logger.info(
                'Korrigerade sv_SE-fältnamn i %d vy(er) '
                '(view-translation-guard)', fixed)
        return fixed
