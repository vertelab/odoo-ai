# -*- coding: utf-8 -*-
"""AI Organization Templates — fördefinierade org-strukturer."""

import json
import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIOrgTemplate(models.Model):
    _name = 'ai.org.template'
    _description = 'AI Organization Template'
    _rec_name = 'name'

    name = fields.Char(required=True)
    industry = fields.Selection([
        ('accounting', 'Redovisningsbyrå'),
        ('consulting', 'Konsultbolag'),
        ('ecommerce', 'E-handel'),
        ('manufacturing', 'Tillverkning'),
        ('saas', 'SaaS-bolag'),
        ('retail', 'Butik'),
        ('custom', 'Anpassad'),
    ], string='Industry')

    description = fields.Text()

    # Strukturen som JSON (laddas från data/templates/*.json)
    structure_json = fields.Json(
        default=dict,
        help='Hela org-strukturen som JSON: departments, coworkers, '
             'agents, goals, skills, tools.')

    active = fields.Boolean(default=True)

    @api.model
    def load_from_file(self, filename):
        """Ladda en template från en JSON-fil i data/templates/."""
        import os
        path = os.path.join(os.path.dirname(__file__),
                           '..', 'data', 'templates', filename)
        path = os.path.abspath(path)
        if not os.path.exists(path):
            _logger.warning('Template file not found: %s', path)
            return False

        with open(path, 'r') as f:
            data = json.load(f)

        existing = self.search([('name', '=', data.get('name'))], limit=1)
        if existing:
            existing.write({
                'industry': data.get('industry'),
                'description': data.get('description'),
                'structure_json': data,
            })
            _logger.info('Updated template: %s', existing.name)
            return existing
        else:
            template = self.create({
                'name': data.get('name', 'Unknown'),
                'industry': data.get('industry'),
                'description': data.get('description'),
                'structure_json': data,
            })
            _logger.info('Created template: %s', template.name)
            return template

    @api.model
    def load_all_templates(self):
        """Ladda alla template-filer från data/templates/."""
        import os, glob
        pattern = os.path.join(os.path.dirname(__file__),
                              '..', 'data', 'templates', '*.json')
        files = glob.glob(os.path.abspath(pattern))
        loaded = 0
        for f in sorted(files):
            filename = os.path.basename(f)
            if self.load_from_file(filename):
                loaded += 1
        _logger.info('Loaded %d templates', loaded)
        return loaded
