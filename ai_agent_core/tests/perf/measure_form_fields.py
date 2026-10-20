# -*- coding: utf-8 -*-
"""Measure the AI coworker form fields — one field at a time.

Standalone script, NOT a unittest (coworker-form-performance F1.1). It opens
the coworker form's fields through the ORM and prints a table
``field -> seconds`` so the same measurement can be repeated before and after
a change (the baseline lives in ``baseline.md`` in the OpenSpec change).

Usage (against a database, from the Odoo shell or with ``odoo shell``)::

    odoo shell -d <db> --no-http < ai_agent_core/tests/perf/measure_form_fields.py

or, with a coworker id and a repeat count::

    odoo shell -d <db> --no-http \
        -c "exec(open('ai_agent_core/tests/perf/measure_form_fields.py').read())" \
        --arg 18

The script also works from a plain ``odoo-bin shell`` session: paste it in
and call ``main(env, coworker_id)``.

Why the ORM and not the web client: the form's cost is the compute calls, not
the rendering. Measuring ``read`` of exactly the fields the form shows makes
the number comparable between two code versions and independent of browser
and network.
"""

import sys
import time

# The fields the coworker form reads (button row + budget/heartbeat panel).
FORM_FIELDS = [
    ('id + name', ['id', 'name']),
    ('id + session_count', ['id', 'session_count']),
    ('id + agent_count', ['id', 'agent_count']),
    ('id + okf_concept_count', ['id', 'okf_concept_count']),
    ('id + session_object_count', ['id', 'session_object_count']),
    ('id + session_line_count', ['id', 'session_line_count']),
    ('id + burn_rate_mtokens_day', ['id', 'burn_rate_mtokens_day']),
    ('id + days_until_budget_empty', ['id', 'days_until_budget_empty']),
    ('id + projected_empty_date', ['id', 'projected_empty_date']),
    ('id + budget_exhausted', ['id', 'budget_exhausted']),
    ('id + daily_budget_used_mtokens', ['id', 'daily_budget_used_mtokens']),
    ('id + heartbeat_status', ['id', 'heartbeat_status']),
]

# All fields the form reads, in one read — the number the spec bounds (< 1 s).
ALL_FORM_FIELDS = [
    'id', 'name', 'agent_count', 'session_count', 'session_object_count',
    'okf_concept_count', 'session_line_count', 'burn_rate_mtokens_day',
    'days_until_budget_empty', 'projected_empty_date', 'budget_exhausted',
    'daily_budget_used_mtokens', 'heartbeat_status',
]


def _time_read(Coworker, coworker_id, fields, repeats=1):
    """Fastest of `repeats` reads, in seconds (cache-cold each time)."""
    best = None
    for _ in range(max(repeats, 1)):
        Coworker.invalidate_model(fields)
        started = time.perf_counter()
        Coworker.browse(coworker_id).read(fields)
        elapsed = time.perf_counter() - started
        best = elapsed if best is None else min(best, elapsed)
    return best


def measure(env, coworker_id, repeats=1, out=None):
    """Measure every form field for one coworker and print a table."""
    out = out or sys.stdout
    Coworker = env['ai.coworker']
    coworker = Coworker.browse(int(coworker_id)).exists()
    if not coworker:
        out.write('coworker %s not found\n' % coworker_id)
        return {}

    lines = env['ai.coworker.session.line'].search_count(
        [('coworker_id', '=', coworker.id)])
    out.write('coworker %s: %s (%s session lines)\n\n'
              % (coworker.id, coworker.name, lines))
    out.write('%-38s %10s\n' % ('field', 'seconds'))
    out.write('-' * 49 + '\n')

    results = {}
    for label, fields in FORM_FIELDS:
        elapsed = _time_read(Coworker, coworker.id, fields, repeats)
        results[label] = elapsed
        out.write('%-38s %10.3f\n' % (label, elapsed))

    total = _time_read(Coworker, coworker.id, ALL_FORM_FIELDS, repeats)
    results['all form fields'] = total
    out.write('-' * 49 + '\n')
    out.write('%-38s %10.3f\n' % ('all form fields', total))
    out.flush()
    return results


def main(env=None, coworker_id=None):
    """Entry point: measure the largest coworker (or the id given)."""
    if env is None:
        # Running inside `odoo shell`: the shell injects `env` and `odoo`.
        env = globals().get('env')
    if env is None:
        sys.stderr.write(
            'No Odoo environment. Run inside `odoo shell -d <db>`.\n')
        return 1
    if not coworker_id:
        Line = env['ai.coworker.session.line']
        groups = Line._read_group(
            [('coworker_id', '!=', False)], ['coworker_id'], ['__count'])
        if not groups:
            sys.stderr.write('No session lines — nothing to measure.\n')
            return 1
        coworker_id = max(groups, key=lambda g: g[1])[0].id
    measure(env, coworker_id)
    return 0


if __name__ == '__main__':
    # `odoo shell` executes this file with `env` in the global namespace.
    sys.exit(main())
