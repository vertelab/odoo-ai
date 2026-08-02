# -*- coding: utf-8 -*-
"""AI Lineage — förklarbara AI-beslut (decision-lineage).

Tunn edge-modell som förenar de spridda spåren (attribution, evidence_ids,
sessioner, förslag, godkännanden) i en frågbar kedja:

  KÄLLA → KONCEPT → KONTEXT → BESLUT → GODKÄNNANDE → ÅTGÄRD

Edge-typer:
  concept_injected      session → concept   (Hermes-injektionen bygger prompten)
  session_to_suggestion session → suggestion (coworkern skapar förslaget)
  suggestion_to_action  suggestion → odoo-objekt (_materialize skapar objektet)
  concept_evidence      suggestion → concept (spegling av evidence_ids)

Regler:
  - ADD-only: edges skapas, raderas aldrig (unlink blockerad).
  - Edge-skapande kastar ALDRIG (try/except) — får inte blockera huvudflödet.
  - from_ref/to_ref (Reference) för läsbarhet; from_model/from_id/to_model/
    to_id (indexerade) för effektiv sökning.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class AILineageLink(models.Model):
    _name = 'ai.lineage.link'
    _description = 'AI Lineage Link — förklarbara AI-beslut'
    _order = 'create_date desc, id desc'

    kind = fields.Selection([
        ('concept_injected', 'Concept Injected'),
        ('session_to_suggestion', 'Session → Suggestion'),
        ('suggestion_to_action', 'Suggestion → Action'),
        ('concept_evidence', 'Concept Evidence'),
    ], string='Kind', required=True, index=True)

    # Referenser (mänskligt läsbara) + relaterade sökfält (indexerade)
    from_ref = fields.Reference(selection='_ref_models', string='From')
    to_ref = fields.Reference(selection='_ref_models', string='To')
    from_model = fields.Char(index=True)
    from_id = fields.Integer(index=True)
    to_model = fields.Char(index=True)
    to_id = fields.Integer(index=True)

    note = fields.Char('Note')

    @api.model
    def _ref_models(self):
        """Palett av referensbara modeller för lineage-edges."""
        return [
            ('ai.coworker.session', 'AI Session'),
            ('ai.okf.concept', 'OKF Concept'),
            ('workspace.activity.suggestion', 'Suggestion'),
            ('ai.personal.memory', 'Personal Memory'),
            ('ai.company.memory', 'Company Memory'),
            ('mail.activity', 'Activity'),
            ('sale.order', 'Sale Order'),
            ('calendar.event', 'Calendar Event'),
            ('dms.file', 'DMS File'),
            ('res.partner', 'Partner'),
        ]

    @api.model
    def _add_edge(self, kind, from_ref, to_ref, note=None):
        """Skapa en lineage-edge — kastar ALDRIG (ADD-only).

        Args:
            kind (str): en av edge-typerna
            from_ref (str): 'model,id' (t.ex. 'ai.coworker.session,42')
            to_ref (str): 'model,id'

        Returns:
            ai.lineage.link | False
        """
        try:
            from_model = from_id = to_model = to_id = None
            if from_ref and isinstance(from_ref, str) and ',' in from_ref:
                from_model, from_id_s = from_ref.split(',', 1)
                from_id = int(from_id_s)
            if to_ref and isinstance(to_ref, str) and ',' in to_ref:
                to_model, to_id_s = to_ref.split(',', 1)
                to_id = int(to_id_s)
            if not from_model or not to_model:
                return False
            return self.create({
                'kind': kind,
                'from_ref': from_ref,
                'to_ref': to_ref,
                'from_model': from_model,
                'from_id': from_id,
                'to_model': to_model,
                'to_id': to_id,
                'note': note,
            })
        except Exception as e:
            _logger.warning(
                'Lineage edge creation failed (%s %s → %s): %s',
                kind, from_ref, to_ref, e)
            return False

    def unlink(self):
        """Edges raderas aldrig (ADD-only)."""
        _logger.warning('Blocked unlink on ai.lineage.link (%s edges)',
                        len(self))
        return False

    # ── Query ──────────────────────────────────────────────────────────

    def _get_lineage(self, model, res_id, direction='backward',
                     max_depth=10):
        """Hämta lineage-kedja för ett objekt.

        Args:
            model (str): modellnamn, t.ex. 'sale.order'
            res_id (int): record-id
            direction (str): 'backward' (objekt → källa) eller
                             'forward' (källa → åtgärder)
            max_depth (int): max hops

        Returns:
            list of dicts: [{kind, from_ref, to_ref, note, create_date}]
        """
        self.ensure_one() if False else None
        edges = []
        seen = set()
        current_model, current_id = model, res_id

        for _hop in range(max_depth):
            if direction == 'backward':
                found = self.search([
                    ('to_model', '=', current_model),
                    ('to_id', '=', current_id),
                ], order='create_date desc', limit=20)
            else:
                found = self.search([
                    ('from_model', '=', current_model),
                    ('from_id', '=', current_id),
                ], order='create_date asc', limit=20)

            if not found:
                break
            progressed = False
            for edge in found:
                key = (edge.id,)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    'kind': edge.kind,
                    'from_ref': edge.from_ref,
                    'to_ref': edge.to_ref,
                    'note': edge.note,
                    'create_date': edge.create_date,
                })
                # Följ kedjan vidare
                if direction == 'backward':
                    if edge.from_model and (edge.from_model, edge.from_id) not in seen:
                        current_model, current_id = edge.from_model, edge.from_id
                        progressed = True
                else:
                    if edge.to_model and (edge.to_model, edge.to_id) not in seen:
                        current_model, current_id = edge.to_model, edge.to_id
                        progressed = True
            if not progressed:
                break

        return edges

    def get_lineage_for_record(self, model, res_id, direction='backward'):
        """Bekväm API-metod för smart-knapp 'Varför?'."""
        return self._get_lineage(model, res_id, direction=direction)
