# -*- coding: utf-8 -*-
"""Svenska relativa tidsuttryck → ett intervall (okf-temporal-recall D3).

VARFÖR EN EGEN FIL, UTAN MODELL: tolkningen är klock- och kalenderkunskap,
inte en domänfråga. Den får därför inte bo på en modell — den ska kunna
anropas från sökvägen, från en cron och från ett test utan `env`.

VARFÖR DETERMINISTISK OCH INTE EN LLM-TUR: samma uttryck måste ge samma
intervall. En LLM-tolkning är icke-deterministisk (samma fråga kan ge olika
svar), kostar en tur per sökning, och går inte att testa mot ett fast `now`.
Spec:en kräver att tolkningen är reproducerbar.

DESIGNVAL — `senaste` ÄR INTE `förra`:

    senaste veckan  = rullande 7 dygn bakåt från nu      (backward)
    förra veckan    = föregående KALENDERvecka           (den förra)
    i veckan        = denna kalendervecka                (båda hållen)

Att slå ihop dem hade gett "förra veckan" ett svar som glider — på en
tisdag hade det blivit förrförra onsdagen till förra tisdagen.

RIKTNING: ett uttryck kan vara `backward` (fråga bakåt), `forward` (framåt)
eller `both` (en namngiven kalenderperiod, t.ex. "i veckan"). Defaulten i
`temporal-recall` använder riktningen för att välja vilket håll ett
ospecificerat fönster ska sträcka sig.

INGEN DOMÄN: filen nämner ingen modell, inget fält, ingen `env`. Den enda
externen är `pytz` för tidszonen.
"""

import re
import unicodedata
from datetime import datetime, timedelta

#: Standardtidszon när användaren saknar en. Samma fallback som
#: `_build_time_schedule_block` använder — en sökning får inte ge olika
#: vecka beroende på vilken kodväg som råkade köra först.
DEFAULT_TZ = 'Europe/Stockholm'


def normalize(text):
    """Lowercase, utan diakriter, kollapsade mellanrum.

    Diakriterna tas bort så att "i morgon" och "i morgon" (samma ord, olika
    tangentbordsnormalisering) matchar. Vi normaliserar till NFKD och
    släpper de kombinerande tecknen — inte till ASCII, för då hade "år"
    blivit "ar" och kolliderat med engelska.
    """
    if not text:
        return ''
    t = unicodedata.normalize('NFKD', str(text))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    return re.sub(r'\s+', ' ', t).strip()


def _tz(name):
    """Hämta en tzinfo; fall tillbaka på DEFAULT_TZ, sedan UTC."""
    try:
        from pytz import timezone
    except ImportError:  # pragma: no cover — pytz är ett Odoo-beroende
        return None
    for candidate in (name, DEFAULT_TZ, 'UTC'):
        if not candidate:
            continue
        try:
            return timezone(candidate)
        except Exception:  # noqa: BLE001 — okänd zon är ett giltigt utfall
            continue
    return None


def _localize(now, tz_name):
    """`now` → medveten datetime i användarens zon.

    Naiv `now` tolkas som LOKAL tid (det är så en användare tänker). En
    redan medveten datetime behåller sin instans men flyttas till zonen —
    annars jämför vi äpplen med päron i veckogränsen.
    """
    zone = _tz(tz_name)
    if zone is None:
        return now
    if now.tzinfo is None:
        return zone.localize(now)
    return now.astimezone(zone)


def _week_bounds(dt):
    """ISO-vecka: måndag 00:00 till söndag 23:59:59.999999 (lokal tid)."""
    monday = dt - timedelta(days=dt.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7) - timedelta(microseconds=1)
    return start, end


def _month_bounds(dt):
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt - timedelta(microseconds=1)


def _year_bounds(dt):
    start = dt.replace(month=1, day=1, hour=0, minute=0, second=0,
                       microsecond=0)
    nxt = start.replace(year=start.year + 1)
    return start, nxt - timedelta(microseconds=1)


def _shift_month(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month, day=1)


def _shift_year(dt, years):
    return dt.replace(year=dt.year + years, month=1, day=1)


#: Uttryckstabellen. ORDNINGEN ÄR VIKTIG: längre/fräshare uttryck först,
#: annars matchar "vecka" före "senaste veckan" och riktningen tappas.
#:
#: Varje post: (uttryck, byggare) där byggaren får (now_lokal) och
#: returnerar (date_from, date_to, direction).
def _rolling(days, direction):
    def build(dt):
        if direction == 'backward':
            return dt - timedelta(days=days), dt, 'backward'
        return dt, dt + timedelta(days=days), 'forward'
    return build


def _calendar(unit, offset):
    """Föregående/nästa kalenderperiod (`offset` = -1, 0, +1)."""
    def build(dt):
        if unit == 'week':
            start, end = _week_bounds(dt)
            if offset:
                start = start + timedelta(days=7 * offset)
                end = start + timedelta(days=7) - timedelta(microseconds=1)
        elif unit == 'month':
            start, end = _month_bounds(dt)
            if offset:
                base = _shift_month(start, offset)
                start, end = _month_bounds(base)
        else:  # year
            start, end = _year_bounds(dt)
            if offset:
                base = _shift_year(start, offset)
                start, end = _year_bounds(base)
        direction = 'both' if offset == 0 else (
            'backward' if offset < 0 else 'forward')
        return start, end, direction
    return build


def _day(offset):
    def build(dt):
        base = (dt + timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return base, base + timedelta(days=1) - timedelta(microseconds=1), \
            ('both' if offset == 0 else
             ('backward' if offset < 0 else 'forward'))
    return build


#: (normaliserat uttryck, byggare). Listan är avsiktligt explicit — en
#: generisk parser hade gissat, och gissningar i en sökväg syns som tomma
#: svar (samma fälla som `min_score`-filtret).
EXPRESSIONS = [
    ('senaste dygnet', _rolling(1, 'backward')),
    ('senaste veckan', _rolling(7, 'backward')),
    ('senaste manaden', _rolling(30, 'backward')),
    ('senaste kvartalet', _rolling(90, 'backward')),
    ('senaste aret', _rolling(365, 'backward')),
    ('senaste veckorna', _rolling(14, 'backward')),
    ('senaste manaderna', _rolling(60, 'backward')),
    ('senaste aren', _rolling(730, 'backward')),
    ('forra veckan', _calendar('week', -1)),
    ('forra manaden', _calendar('month', -1)),
    ('forra aret', _calendar('year', -1)),
    ('nasta vecka', _calendar('week', +1)),
    ('nasta manad', _calendar('month', +1)),
    ('nasta ar', _calendar('year', +1)),
    ('den har veckan', _calendar('week', 0)),
    ('den har manaden', _calendar('month', 0)),
    ('det har aret', _calendar('year', 0)),
    ('denna vecka', _calendar('week', 0)),
    ('denna manad', _calendar('month', 0)),
    ('i veckan', _calendar('week', 0)),
    ('i manaden', _calendar('month', 0)),
    ('i ar', _calendar('year', 0)),
    ('idag', _day(0)),
    ('igar', _day(-1)),
    ('i morgon', _day(+1)),
    ('imorgon', _day(+1)),
    ('i overmorgon', _day(+2)),
]


def _word_boundary_pattern(phrase):
    """Regex som kräver ORDGRÄNS kring frasen.

    Utan den matchar "i veckan" inuti "veckanlång" och "senaste aret"
    inuti ett sammansatt ord — och då får en fråga om något helt annat ett
    tidsfönster.
    """
    return re.compile(r'(?<![\w])' + re.escape(phrase) + r'(?![\w])',
                      re.UNICODE)


_COMPILED = [(_word_boundary_pattern(p), p, b) for p, b in EXPRESSIONS]


def resolve(text, now=None, tz_name=None):
    """Tolka ett relativt tidsuttryck i `text`.

    Args:
        text: frågetexten (eller bara uttrycket — båda fungerar, vi söker
            efter kända fraser med ordgräns).
        now: ankartid. Naiv = lokal tid i `tz_name`. Default: nu.
        tz_name: användarens zon (`res.users.tz`).

    Returns:
        dict med
            date_from (datetime|None)  — fönstrets start, lokal tid
            date_to   (datetime|None)  — fönstrets slut, lokal tid
            direction ('backward'|'forward'|'both'|None)
            matched   (str|None)       — frasen som tolkades
            unmatched (str|None)       — tidsliknande fras vi INTE förstod

    Ärligt tomt: kan inget tolkas är `date_from`/`date_to` None och
    `direction` None. Sökningen får då inget fönster — den gissar aldrig.
    """
    norm = normalize(text)
    empty = {'date_from': None, 'date_to': None, 'direction': None,
             'matched': None, 'unmatched': None}
    if not norm:
        return empty

    anchor = _localize(now or datetime.now(), tz_name)

    for pattern, phrase, build in _COMPILED:
        if pattern.search(norm):
            date_from, date_to, direction = build(anchor)
            return {'date_from': date_from, 'date_to': date_to,
                    'direction': direction, 'matched': phrase,
                    'unmatched': None}

    # Ingen känd fras. Leta efter något som SER ut som ett tidsuttryck, så
    # att svaret kan förklara varför inget fönster sattes — tystnaden ska
    # vara namngiven (samma ärlighetskrav som trösklarna).
    suspicious = re.search(
        r'(?<![\w])(i|forra|nasta|senaste|denna|den har|det har)\s+[a-z]+',
        norm)
    if suspicious:
        return dict(empty, unmatched=suspicious.group(0))
    return empty


def window_for(default, now=None, tz_name=None):
    """Räkna fram ett fönster ur en registrerad tidsdefault.

    `default` är `{'days': int, 'direction': 'backward'|'forward'}` — exakt
    den form `_okf_register_temporal_default()` lagrar. Saknas den blir
    svaret `(None, None, None)`: ingen tidsvikt, dagens beteende.
    """
    if not default:
        return None, None, None
    try:
        days = int(default.get('days') or 0)
    except (TypeError, ValueError):
        return None, None, None
    if days <= 0:
        return None, None, None
    direction = default.get('direction') or 'backward'
    if direction not in ('backward', 'forward'):
        direction = 'backward'
    anchor = _localize(now or datetime.now(), tz_name)
    if direction == 'backward':
        return anchor - timedelta(days=days), anchor, direction
    return anchor, anchor + timedelta(days=days), direction


def build_boost_sql(defaults, explicit_window=None, now=None, tz_name=None,
                    artifact_type_field='artifact_type_id'):
    """Bygg SQL-uttrycket för tidsvikten (okf-temporal-recall D2).

    Vikten är en ADDITIV term i `score` — aldrig ett `WHERE`. Skälet står i
    design D2: ett hårt filter gör sökningen tyst blind, och `@@`-grinden
    är redan den enda relevansgrind vi litar på.

        boost = 1                              inne i fönstret
                1 - (avstånd / fönsterlängd)    utanför, avtagande
                0                              utan källdatum

    Fönstret väljs per ARTEFAKTTYP: ett mailkoncept får mail-fönstret (bakåt),
    ett kalenderkoncept kalenderfönstret (framåt) — i samma sökning. Det är
    därför uttrycket är en CASE på typen och inte ett enda intervall.

    ETT EXPLICIT FÖNSTER (`explicit_window`) gäller ALLA typer: frågar
    användaren "senaste året" är det året som avses, oavsett typ. Defaulten
    tillämpas då inte ovanpå.

    Args:
        defaults: `{artifact_type_name: {'days': int, 'direction': str}}`
        explicit_window: `(date_from, date_to)` eller None
        now: ankartid för defaultens fönster
        tz_name: användarens zon
        artifact_type_field: kolumnnamnet i frågan

    Returns:
        (sql_fragment, params) — fragmentet är ett uttryck som ger en float
        i [0, 1]. Utan fönster alls: `('0', {})`.
    """
    params = {}

    def _one(prefix, date_from, date_to):
        if date_from is None or date_to is None:
            return '0'
        total = (date_to - date_from).total_seconds()
        if total <= 0:
            return '0'
        params[prefix + 'f'] = date_from
        params[prefix + 't'] = date_to
        params[prefix + 'len'] = float(total)
        return (
            "CASE WHEN source_date IS NULL THEN 0"
            " WHEN source_date >= %(" + prefix + "f)s"
            "  AND source_date <= %(" + prefix + "t)s THEN 1"
            " ELSE GREATEST(0, 1 - ("
            "   CASE WHEN source_date < %(" + prefix + "f)s"
            "        THEN EXTRACT(EPOCH FROM (%(" + prefix + "f)s - source_date))"
            "        ELSE EXTRACT(EPOCH FROM (source_date - %(" + prefix + "t)s))"
            "   END) / %(" + prefix + "len)s) END"
        )

    # Explicit fönster: en term, ingen CASE på typ.
    if explicit_window:
        return _one('twx', explicit_window[0], explicit_window[1]), params

    if not defaults:
        return '0', params

    cases = []
    for idx, (type_name, cfg) in enumerate(sorted(defaults.items())):
        date_from, date_to, _dir = window_for(cfg, now=now, tz_name=tz_name)
        if date_from is None:
            continue
        term = _one('tw%d' % idx, date_from, date_to)
        if term == '0':
            continue
        prefix = 'twat%d' % idx
        params[prefix] = type_name
        # JOIN i stället för en korrelerad subselect per rad: fönstret ska
        # kosta ett uppslag, inte ett per koncept. `%s` är kolumnnamnet
        # (kodägd konstant, aldrig användardata).
        cases.append(
            "WHEN %s = (SELECT id FROM ai_artifact_type WHERE name = %%(%s)s)"
            " THEN %s" % (artifact_type_field, prefix, term))
    if not cases:
        return '0', params
    return 'CASE ' + ' '.join(cases) + ' ELSE 0 END', params
