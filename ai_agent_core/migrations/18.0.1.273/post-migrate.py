# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.273: finish_reason Char → Selection (utfall-och-tokenmatning D3).

`ai.coworker_session.finish_reason` var en Char. Den blir en Selection med
14 värden. Befintliga strängar mappas uttömmande; okända strängar (t.ex. den
gamla `str(e)[:200]`-konventionen) mappas till `error` och originaltexten
flyttas till `error_detail` så att ingen information tappas.

Mappningen är avsiktligt generös: `end_turn` (Anthropics stop_reason) → `stop`,
och allt som inte känns igen → `error` + bevarad text.
"""

import logging

_logger = logging.getLogger(__name__)

#: Kända värden → Selections värde. Speglar models/ai_session.py.
KNOWN = {
    'stop': 'stop',
    'length': 'length',
    'tool_calls': 'tool_calls',
    'content_filter': 'content_filter',
    'refusal': 'refusal',
    'max_rounds': 'max_rounds',
    'max_tokens': 'max_tokens',
    'timeout': 'timeout',
    'cancelled': 'cancelled',
    'error': 'error',
    'idle': 'idle',
    'closed': 'closed',
    'interrupted': 'interrupted',
    'new_session': 'new_session',
    'setup_failed': 'setup_failed',
    # Provider-aliaser som ska normaliseras, inte bli 'error'.
    'end_turn': 'stop',
    'stop_sequence': 'stop',
    'function_call': 'tool_calls',
}


def migrate(cr, version):
    _logger.info("Running migration 18.0.1.273: finish_reason Char → Selection")

    # Kolumnen är redan varchar (Char) — Selection lagras också som varchar,
    # så ingen typändring behövs. Vi normaliserar bara värdena.
    cr.execute("""
        SELECT id, finish_reason
          FROM ai_coworker_session
         WHERE finish_reason IS NOT NULL
           AND finish_reason != ''
    """)
    rows = cr.fetchall()

    mapped = 0
    to_error = 0
    for sid, raw in rows:
        value = (raw or '').strip()
        if value in KNOWN:
            new = KNOWN[value]
            if new != value:
                cr.execute(
                    "UPDATE ai_coworker_session SET finish_reason = %s WHERE id = %s",
                    (new, sid))
            mapped += 1
        else:
            # Okänt (t.ex. fritext från gamla `str(e)[:200]`): typa som error
            # och bevara originaltexten i error_detail.
            cr.execute("""
                UPDATE ai_coworker_session
                   SET finish_reason = 'error',
                       error_detail = COALESCE(error_detail, %s)
                 WHERE id = %s
            """, (value[:4000], sid))
            to_error += 1

    _logger.info(
        "Migration 18.0.1.273: %d rader mappade, %d okända → error (+error_detail)",
        mapped, to_error)
