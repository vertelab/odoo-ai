# -*- coding: utf-8 -*-
"""Kanalprestanda — mät samma fråga över Pi, Web UI och DM.

Fristående skript, INTE en unittest (kanalprestanda D5). Samma form som
``measure_form_fields.py``: körs via ``odoo shell`` mot en lokal testdatabas
och skriver en tabell. En prestandamätning som går genom ``TransactionCase``
mäter testramverkets overhead och kan inte köras mot en databas med
realistisk historik.

Körning::

    odoo shell -d <testdb> --no-http \\
        < ai_agent_core/tests/perf/measure_channels.py

eller med repetitioner::

    odoo shell -d <testdb> --no-http \\
        -c "exec(open('ai_agent_core/tests/perf/measure_channels.py').read())" \\
        --arg 5

Vad som mäts, per assistentrad:

* **TTFT** — ``first_token_seconds`` (0 för icke-strömmande kanaler)
* **strömning** — ``stream_seconds``
* **totalt** — väggklocka ur user-radens ``create_date`` → assistentradens
  ``create_date`` (raderna har redan ``_log_access`` och indexerade
  tidsstämplar; inget nytt fält behövs)

Kanalerna anropas på sina riktiga ingångar (D5): DM via
``ai.coworker.chat()``, Web UI via sessionsskapande + ``run()`` (den
synkrona kärnan i ``/ai/stream``), Pi via ``/ai/v1``-persistensvyn. Att
anropa HTTP från ett shell-skript hade krävt en levande server och en
inloggning — det hör till en deploy-verifiering, inte utvecklingsgrinden.

⚠ **Omätt kanal fäller körningen** (spec: "Jämförelseverktyget är
reproducerbart och vägrar gissa"). En tom tabell får inte se ut som ett
resultat.
"""

import math
import sys
import time

#: Kanalerna och deras ``init_type`` på sessionen.
CHANNELS = ('openai_api', 'web_ui', 'chat')

#: Fast frågeuppsättning — samma frågor varje körning, så två körningar är
#: jämförbara. Korta medvetet: det är kanalens overhead som mäts, inte
#: modellens svarstid på en lång uppgift.
QUESTIONS = (
    'Vad är klockan?',
    'Sammanfatta vad du kan hjälpa till med i en mening.',
)

#: Prefix på sessionsnamnen verktyget skapar. Används för att hitta sina
#: egna sessioner (inte för att tidsfiltrera hela tabellen).
SESSION_PREFIX = 'perf-channel-'


# ── Statistik ───────────────────────────────────────────────────────────

def _median(values):
    """Median ur en lista flyttal (None för tom lista)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _p95(values):
    """95:e percentilen (nearest-rank) ur en lista flyttal.

    nearest-rank: index = ceil(0.95 × n) − 1. För ``[1,2,3,4,5]`` ger det
    index 4 (värdet 5), för 20 värden index 18 — inte maxvärdet, som
    ``round`` hade gett.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(0.95 * len(ordered)) - 1, 0)
    return ordered[min(rank, len(ordered) - 1)]


# ── Insamling ───────────────────────────────────────────────────────────

def _pick_coworker(env):
    """Välj en medarbetare som kan svara — helst en med web_ui-initiering."""
    Coworker = env['ai.coworker']
    candidates = Coworker.search([], limit=50)
    for coworker in candidates:
        if any(it.init_type == 'web_ui' and it.enabled
               for it in coworker.init_type_ids):
            return coworker
    return candidates[:1] or Coworker.browse(0)


def _collect_session_lines(env, session_ids):
    """Läs de assistentrader verktyget själv skapade.

    ``session_ids`` är de sessioner verktyget skapade under körningen —
    ingen tidsfiltrering av hela tabellen (D5). Varje rad är en mätpunkt;
    ett retry (samma ``content_hash``, annat tokenantal) är ett eget,
    betalat anrop och slås INTE ihop (D6).
    """
    if not session_ids:
        return []
    Line = env['ai.coworker.session.line'].sudo()
    lines = Line.search([
        ('session_id', 'in', list(session_ids)),
        ('role', '=', 'assistant'),
    ], order='id asc')
    points = []
    for line in lines:
        session = line.session_id
        # Total väggklocka: user-radens create_date → denna rads.
        user_line = Line.search([
            ('session_id', '=', session.id),
            ('role', '=', 'user'),
            ('create_date', '<=', line.create_date),
        ], order='create_date desc, id desc', limit=1)
        total = None
        if user_line and user_line.create_date and line.create_date:
            total = (line.create_date - user_line.create_date).total_seconds()
        points.append({
            'channel': session.init_type,
            'line_id': line.id,
            'ttft': line.first_token_seconds or 0.0,
            'stream': line.stream_seconds or 0.0,
            'gap': line.longest_gap_seconds or 0.0,
            'total': total,
        })
    return points


def _run_turn(env, coworker, init_type, prompt):
    """Kör en tur på kanalens riktiga ingång och returnera sessionen.

    * ``chat`` — ``ai.coworker.chat()`` (DM:ens synkrona väg).
    * ``web_ui`` / ``openai_api`` — sessionsskapande + ``run()``, den
      synkrona kärnan i respektive väg. Skillnaden är ``init_type``, som är
      den axel mätningen grupperar på.

    Returnerar sessionen (eller en tom recordset om kanalen inte kunde
    köras — då blir kanalen "omätt" och fäller körningen).
    """
    Session = env['ai.coworker.session'].sudo()
    session = Session.create({
        'coworker_id': coworker.id,
        'name': '%s%s-%d' % (SESSION_PREFIX, init_type, int(time.time() * 1000)),
        'status': 'active',
        'init_type': init_type,
    })
    try:
        if init_type == 'chat':
            # DM-vägen kräver en discus-kanal och en bot-partner; utan dem
            # är kanalen omätt (rapporteras, gissas inte).
            channel = env['discuss.channel'].sudo().search(
                [('channel_type', '=', 'chat')], limit=1)
            if not channel:
                return Session.browse(0)
            env['mail.message'].sudo().create({
                'model': 'discuss.channel',
                'res_id': channel.id,
                'body': prompt,
                'message_type': 'comment',
            })
            coworker.chat(channel.message_ids[:1], channel=channel)
        else:
            coworker.run(prompt, session=session)
    except Exception as exc:  # noqa: BLE001 — en kanal som kastar är omätt
        sys.stderr.write('%s: turen kastade %s: %s\n'
                         % (init_type, type(exc).__name__, exc))
        return session
    return session


# ── Rapport ─────────────────────────────────────────────────────────────

def _report(points, out):
    """Skriv en rad per kanal med median, p95 och n."""
    out.write('%-12s %6s %10s %10s %10s %10s %10s %10s\n' % (
        'kanal', 'n', 'TTFT med', 'TTFT p95', 'ström med', 'ström p95',
        'tot med', 'tot p95'))
    out.write('-' * 86 + '\n')
    measured = {}
    for channel in CHANNELS:
        rows = [p for p in points if p['channel'] == channel]
        measured[channel] = len(rows)
        if not rows:
            out.write('%-12s %6d %10s\n' % (channel, 0, 'OMÄTT'))
            continue
        ttft = [p['ttft'] for p in rows]
        stream = [p['stream'] for p in rows]
        total = [p['total'] for p in rows if p['total'] is not None]
        out.write('%-12s %6d %10.3f %10.3f %10.3f %10.3f %10.3f %10.3f\n' % (
            channel, len(rows),
            _median(ttft), _p95(ttft),
            _median(stream), _p95(stream),
            _median(total) if total else 0.0,
            _p95(total) if total else 0.0,
        ))
    out.flush()
    return measured


def main(env=None, repeat=1, out=None):
    """Kör frågeuppsättningen ``repeat`` gånger per kanal och rapportera.

    Returnerar 0 när alla kanaler gav mätpunkter, 1 när någon kanal är
    omätt (spec: körningen ska avslutas med fel status).
    """
    out = out or sys.stdout
    if env is None:
        env = globals().get('env')
    if env is None:
        sys.stderr.write(
            'No Odoo environment. Run inside `odoo shell -d <db>`.\n')
        return 1
    repeat = max(int(repeat or 1), 1)
    coworker = _pick_coworker(env)
    if not coworker:
        sys.stderr.write('Ingen ai.coworker i databasen — inget att mäta.\n')
        return 1
    out.write('medarbetare: %s (%s)\n' % (coworker.id, coworker.name))
    out.write('repetitioner per fråga: %d, frågor: %d\n\n'
              % (repeat, len(QUESTIONS)))

    session_ids = []
    for channel in CHANNELS:
        for _ in range(repeat):
            for prompt in QUESTIONS:
                session = _run_turn(env, coworker, channel, prompt)
                if session:
                    session_ids.append(session.id)

    points = _collect_session_lines(env, session_ids)
    measured = _report(points, out)

    unmet = [c for c in CHANNELS if not measured.get(c)]
    if unmet:
        sys.stderr.write(
            '\nOMÄTT: %s gav inga mätpunkter. En tom rad är inte ett '
            'resultat — konfigurera kanalen eller stryk den ur körningen.\n'
            % ', '.join(unmet))
        return 1
    return 0


if __name__ == '__main__':
    # `odoo shell` kör filen med `env` i globala namnutrymmet.
    _repeat = 1
    if len(sys.argv) > 1:
        try:
            _repeat = int(sys.argv[1])
        except ValueError:
            _repeat = 1
    sys.exit(main(repeat=_repeat))
