# -*- coding: utf-8 -*-
"""ai.eval.case / ai.eval.run / ai.eval.result — persistens för core/eval.py.

Ändring: wire-orphaned-core-mechanisms (sektion 3–6).

`core/eval.py` var komplett men hade noll anropare: `AgentEvaluator` och
`EvalRunner` kunde mäta en agent, men det fanns ingenstans att spara
fallen eller resultaten. Här finns persistensen och den enda vägen in:

    ai.coworker.session (finish_reason ∈ {error, max_rounds, length, timeout})
            │  (knappen på sessionsformuläret)  — HITL, människan väljer
            ▼
    ai.eval.case  (input = sessionens första user-rad, source_session_id)
            │  ai.coworker._cron_run_evals()   — veckovis
            ▼
    ai.eval.run  → ai.kaizen.report.eval_accuracy_trend

Två saker är avsiktliga och får inte "förbättras" utan beslut:

* **Ingen automatisk befordran.** En misslyckad session är inte
  automatiskt ett bra testfall. `expected_contains` måste sättas av en
  människa — annars poängsätter testet "gav samma svar som förra gången",
  vilket är motsatsen till regressionstest (samma princip som
  `ai.kaizen.finding`: förslag → godkännande).
* **`source_session_id` är obligatoriskt.** Ett testfall som inte kan
  härledas till en verklig händelse är en gissning.
"""

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def _json_list(value, field_label):
    """Läs ett JSON-fält som en lista av strängar.

    Tomt fält → tom lista. Ett värde som inte är en JSON-lista avvisas med
    ett fel som namnger fältet — annars blir en trasig förväntan tyst ett
    testfall som alltid passerar (tom `expected_contains` ger score 1.0).
    """
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            _('%(field)s must be a JSON list of strings: %(error)s',
              field=field_label, error=exc)) from exc
    if not isinstance(data, list):
        raise ValidationError(
            _('%(field)s must be a JSON list of strings.', field=field_label))
    return [str(item) for item in data]


class AIEvalCase(models.Model):
    _name = 'ai.eval.case'
    _description = 'AI Evaluation Case'
    _order = 'id desc'

    coworker_id = fields.Many2one(
        'ai.coworker', string='Coworker', required=True, ondelete='cascade',
        index=True,
        help='Medarbetaren testet hör till. En medarbetare byter agent '
             'över tid — testet hör till medarbetaren, inte till agenten.')
    source_session_id = fields.Many2one(
        'ai.coworker.session', string='Source Session', required=True,
        ondelete='cascade', index=True,
        help='Sessionen testfallet befordrades från. Spårbarhet: ett '
             'testfall som inte kan härledas till en verklig händelse är '
             'en gissning.')
    input = fields.Text(
        'Input', required=True,
        help='Prompten som ska köras. Sätts från sessionens första '
             'user-rad vid befordran.')
    expected_contains = fields.Text(
        'Expected Contains (JSON)',
        help='JSON-lista av strängar som svaret MÅSTE innehålla. '
             'Tom lista = inget krav (testet mäter då bara frånvaro).')
    expected_not_contains = fields.Text(
        'Expected Not Contains (JSON)',
        help='JSON-lista av strängar som svaret INTE får innehålla.')
    category = fields.Char(
        'Category', default='general',
        help='Fri kategori för gruppering (t.ex. "tool_use", "odoo_write").')
    active = fields.Boolean('Active', default=True)

    _sql_constraints = [
        ('source_session_uniq',
         'UNIQUE(source_session_id)',
         'En session kan bara befordras en gång.'),
    ]

    @api.constrains('expected_contains', 'expected_not_contains')
    def _check_expected_lists(self):
        for case in self:
            _json_list(case.expected_contains, 'expected_contains')
            _json_list(case.expected_not_contains, 'expected_not_contains')

    def _to_eval_case(self):
        """Bygg en `core.eval.EvalCase` av posten.

        `expected_output` lämnas tomt: `EvalRunner` poängsätter då bara mot
        contains-listorna, vilket är det enda en människa kan fylla i från
        en misslyckad session utan att gissa.
        """
        self.ensure_one()
        from odoo.addons.ai_agent_core.core.eval import EvalCase
        return EvalCase(
            input=self.input or '',
            expected_output='',
            expected_contains=_json_list(
                self.expected_contains, 'expected_contains'),
            expected_not_contains=_json_list(
                self.expected_not_contains, 'expected_not_contains'),
            category=self.category or 'general',
        )


class AIEvalRun(models.Model):
    _name = 'ai.eval.run'
    _description = 'AI Evaluation Run'
    _order = 'run_date desc, id desc'

    coworker_id = fields.Many2one(
        'ai.coworker', string='Coworker', required=True, ondelete='cascade',
        index=True)
    run_date = fields.Datetime(
        'Run Date', default=fields.Datetime.now, required=True, index=True)
    accuracy = fields.Float(
        'Accuracy', digits=(5, 4),
        help='Andel godkända fall (0.0–1.0).')
    passed = fields.Integer('Passed', default=0)
    failed = fields.Integer('Failed', default=0)
    total_cases = fields.Integer('Total Cases', default=0)
    total_cost = fields.Float(
        'Total Cost (USD)', digits=(12, 6),
        help='Summan av `EvalResult.cost`. Priserna är hårdkodade i '
             'core/eval.py och är en uppskattning — inte en faktura.')
    result_ids = fields.One2many(
        'ai.eval.result', 'run_id', string='Results')

    def _write_accuracy_to_kaizen(self):
        """Skriv runnets accuracy till veckorapporten (task 5.5).

        Fältet `eval_accuracy_trend` på `ai.kaizen.report` är trendkällan
        för `_analyze_findings()`: när den sjunker skapas ett
        `performance`-fynd. Vi skriver till den SENASTE rapporten för
        medarbetaren — rapporten är veckans mätpunkt, och runnet hör till
        samma vecka.

        Ingen rapport finns (t.ex. en medarbetare utan aktivitet) → inget
        att skriva till. Det är inte ett fel: runnet finns kvar på
        `ai.eval.run` och nästa veckorapport får sitt eget värde.
        """
        for run in self:
            if not run.coworker_id:
                continue
            report = self.env['ai.kaizen.report'].search(
                [('coworker_id', '=', run.coworker_id.id)],
                order='week_start desc, id desc', limit=1)
            if not report:
                _logger.info(
                    'Eval: inget kaizen-rapport för %s — accuracy %.2f '
                    'sparas bara på runnet', run.coworker_id.name,
                    run.accuracy)
                continue
            report.eval_accuracy_trend = run.accuracy


class AIEvalResult(models.Model):
    _name = 'ai.eval.result'
    _description = 'AI Evaluation Result'
    _order = 'run_id desc, id asc'

    run_id = fields.Many2one(
        'ai.eval.run', string='Run', required=True, ondelete='cascade',
        index=True)
    case_id = fields.Many2one(
        'ai.eval.case', string='Case', required=True, ondelete='cascade',
        index=True)
    passed = fields.Boolean('Passed')
    score = fields.Float('Score', digits=(5, 4))
    actual_output = fields.Text('Actual Output')
    errors = fields.Text('Errors')
    latency_ms = fields.Float('Latency (ms)')
