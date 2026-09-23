# -*- coding: utf-8 -*-
"""ai.okf.tag — taggar för OKF-koncept.

VARFÖR EN EGEN MODELL OCH INTE ETT JSON-FÄLT:

Ett koncept kan bära taggar från flera källor (en webbsida taggas med
sajtens namn, ett blogginlägg med bloggens, en affär med säljteamet).
Med JSON blir "alla koncept med taggen X" en jsonb-sökning per rad.
Med en many2many blir det en JOIN — och Odoo:s `many2many_tags`-widget
fungerar direkt i vyn.

Taggen är en ETIKETT, inte ett koncept: den har ett namn och inget
innehåll att indexera. Därför får den ingen egen okf-mixin — den hör
till konceptets frontmatter (`tags:` i OKF), inte till dess länkar.
"""

from odoo import models, fields


class AIOkfTag(models.Model):
    _name = 'ai.okf.tag'
    _description = 'OKF Tag'
    _order = 'name asc'

    name = fields.Char('Name', required=True, index=True)
    color = fields.Integer('Color', default=0)

    concept_ids = fields.Many2many(
        'ai.okf.concept', 'ai_okf_concept_tag_rel',
        'tag_id', 'concept_id', string='Concepts')

    _sql_constraints = [
        ('name_uniq', 'UNIQUE(name)', 'Tag names must be unique.'),
    ]
