# -*- coding: utf-8 -*-
"""Mät en tur på BÅDE kvalitet och tid (tool-call-quality D7).

VARFÖR ETT EGET SKRIPT OCH INTE ETT VANLIGT TEST: en mätning som går genom
`TransactionCase` mäter testramverkets overhead, och en mätning som mockar
loopen mäter ingenting om verktygsval. Samma form som `measure_channels.py`
av samma skäl.

Men till skillnad från `measure_channels.py` mäter den här **kvalitet
också**. De två mätningarna fanns tidigare i skilda världar:

    test_channel_timing.py   → tid (TTFT, gap, stream)
    test_eval_pipeline.py    → kvalitet (men loopen mockas)

Ingen plats hade båda. Följden: session 28097 tog 114 sekunder till första
verktygssvar och svarade "(inget svar)" — och ingen mätning kunde fånga
kombinationen.

FRÅGAN SOM MÄTS kräver TRE källor i samma svar:

    mail      — vad som skrevs
    kalender  — vad som är bokat
    relaterat — vilka kunder/uppgifter som hör ihop med det

En fråga som en enda modell kan svara på ur minnet mäter ingenting om
verktygsval.

Körning::

    odoo shell -d <testdb> --no-http \\
        < ai_agent_core/tests/perf/measure_turn_quality.py
"""

#: Frågan som mäts. Den är avsiktligt sammansatt: en fråga som bara gäller
#: mail hade kunnat besvaras med ett enda `odoo_search`, och då mäter den
#: inte om agenten väljer rätt verktyg för en fråga som spänner över flera
#: modeller.
MEASURE_QUESTION = (
    "Vad har jag för möten med Skanska den här veckan, och vad skrev de "
    "i sina senaste mail? Vilka uppgifter hör till?"
)

#: Innehåll som MÅSTE finnas i ett godkänt svar. Varje post motsvarar en
#: av de tre källorna — ett svar som bara täcker mail är inte godkänt.
EXPECTED_CONTAINS = [
    'Skanska',      # kunden — den gemensamma nämnaren
]

#: Innehåll som INTE får finnas: ett svar som ger upp är inte godkänt även
#: om det är snabbt.
EXPECTED_NOT_CONTAINS = [
    'inget svar',
    'jag kan inte',
]

#: Tidsgränser. TTFT mäts i sekunder; verktygsantalet är det som visade hur
#: många sonderande anrop en tur behövde (13 i session 28097).
MAX_FIRST_TOKEN_SECONDS = 20.0
MAX_TOOL_CALLS = 6


def measure(env, coworker_name=None, question=None):
    """Kör frågan och returnera de tre måtten sida vid sida.

    Returns:
        dict med `first_token`, `tool_calls`, `quality` och `verdict`.
        `verdict` namnger VILKEN dimension som fällde körningen — ett
        sammansatt index hade gjort det omöjligt att se.
    """
    import json
    question = question or MEASURE_QUESTION
    Coworker = env['ai.coworker']
    if coworker_name:
        coworker = Coworker.search([('name', '=', coworker_name)], limit=1)
    else:
        coworker = Coworker.search([('status', '=', 'active')], limit=1)
    if not coworker:
        return {'error': 'no coworker found — pass coworker_name'}

    session = env['ai.coworker.session'].create({
        'name': question[:60],
        'coworker_id': coworker.id,
        'user_id': env.user.id,
    })
    try:
        coworker.run(question, session=session)
    except Exception as e:  # noqa: BLE001 — mätningen ska rapportera, inte krascha
        return {'error': 'run failed: %s' % e, 'session_id': session.id}

    lines = session.session_line_ids.sorted(
        key=lambda l: (l.sequence or 0, l.id or 0))
    assistant = [l for l in lines if l.role == 'assistant']
    tool_lines = [l for l in lines if l.role == 'tool']
    answer = (assistant[-1].content if assistant else '') or ''
    timing = assistant[-1] if assistant else None

    first_token = getattr(timing, 'first_token_seconds', 0.0) or 0.0
    tool_calls = len(tool_lines)

    answer_lower = answer.lower()
    missing = [w for w in EXPECTED_CONTAINS if w.lower() not in answer_lower]
    forbidden = [w for w in EXPECTED_NOT_CONTAINS if w.lower() in answer_lower]
    quality_ok = not missing and not forbidden

    reasons = []
    if not quality_ok:
        if missing:
            reasons.append('kvalitet: saknar %s' % ', '.join(missing))
        if forbidden:
            reasons.append('kvalitet: innehåller %s' % ', '.join(forbidden))
    if first_token > MAX_FIRST_TOKEN_SECONDS:
        reasons.append('tid: TTFT %.1fs > %.0fs'
                       % (first_token, MAX_FIRST_TOKEN_SECONDS))
    if tool_calls > MAX_TOOL_CALLS:
        reasons.append('tid: %d verktygsanrop > %d'
                       % (tool_calls, MAX_TOOL_CALLS))

    return {
        'session_id': session.id,
        'first_token': round(first_token, 2),
        'tool_calls': tool_calls,
        'quality_ok': quality_ok,
        'missing': missing,
        'forbidden': forbidden,
        'verdict': 'PASS' if not reasons else 'FAIL',
        'reasons': reasons,
        'answer_preview': answer[:400],
    }


def _print(result):
    if 'error' in result:
        print('ERROR:', result['error'])
        return
    print('=' * 60)
    print('Fråga:', MEASURE_QUESTION[:70])
    print('-' * 60)
    print('  TTFT          : %.2f s   (gräns %.0f)'
          % (result['first_token'], MAX_FIRST_TOKEN_SECONDS))
    print('  Verktygsanrop : %d        (gräns %d)'
          % (result['tool_calls'], MAX_TOOL_CALLS))
    print('  Kvalitet      : %s' % ('OK' if result['quality_ok'] else 'UNDERKÄND'))
    print('-' * 60)
    print('  Utfall: %s' % result['verdict'])
    for r in result['reasons']:
        print('    - %s' % r)
    print('-' * 60)
    print('  Svar (utdrag):', result['answer_preview'][:300])
    print('=' * 60)


# Körs bara när skriptet matas till `odoo shell` — INTE vid import.
# `env` finns bara i shell-kontexten; att anropa `measure()` vid import hade
# kraschat varje test som importerar modulen för att läsa konstanterna.
if __name__ == '__main__' or 'env' in globals():
    try:
        _print(measure(env))  # noqa: F821 — `env` finns i odoo shell
    except NameError:
        pass  # importerad som modul, ingen shell-kontext
