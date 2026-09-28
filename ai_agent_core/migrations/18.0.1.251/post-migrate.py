"""18.0.1.251 — YouTube-verktygens felmeddelanden får åtgärdbar struktur.

FYND 2026-09-23: de fem "No Google API key configured"-grenarna i
data/youtube_tools.xml returnerade `json.dumps({"error": ...})` — utan
nycklarna `parameter`, `expected`, `actual`, `retryable`. Alla andra
åtgärdbara fel i modulen går via `_tool_error()`. LLM:en fick därför ett
fel den inte kunde agera på (vilken parameter? vad förväntades?), vilket
test_actionable_errors fångade.

data/youtube_tools.xml har `noupdate="1"` — datafilen laddas bara vid
första installation, så en vanlig `--update` skriver INTE över befintliga
ai.tool-poster. Denna migration läser om datafilen och skriver in den nya
koden på de fyra verktygen.

Idempotent: verktyg vars kod redan innehåller `parameter="api_key"` i
felgrenen lämnas orörda.
"""
import logging
import os
import xml.etree.ElementTree as ET

_logger = logging.getLogger(__name__)

_TOOL_NAMES = (
    'youtube_search', 'youtube_channel',
    'youtube_playlist', 'youtube_get_transcript',
)


def _code_from_datafile():
    """Läs verktygskoden ur data/youtube_tools.xml.

    Returnerar {name: code}. Vi läser filen i stället för att hårdkoda
    strängar: då följer migrationen automatiskt med om datafilen ändras
    igen (och riskerar inte att glida isär från sanningen).
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        'data', 'youtube_tools.xml')
    if not os.path.exists(path):
        _logger.warning('18.0.1.251: datafilen saknas: %s', path)
        return {}
    root = ET.parse(path).getroot()
    out = {}
    for rec in root.iter('record'):
        if rec.get('model') != 'ai.tool':
            continue
        name = code = None
        for f in rec.findall('field'):
            if f.get('name') == 'name':
                name = (f.text or '').strip()
            elif f.get('name') == 'code':
                code = f.text
        if name and code:
            out[name] = code
    return out


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    Tool = env['ai.tool']
    fresh = _code_from_datafile()

    updated, skipped = [], []
    for name in _TOOL_NAMES:
        tool = Tool.search([('name', '=', name)], limit=1)
        if not tool:
            continue
        new_code = fresh.get(name)
        if not new_code:
            continue
        if tool.code == new_code:
            skipped.append(name)
            continue
        tool.code = new_code
        updated.append(name)

    _logger.info(
        '18.0.1.251: YouTube-verktyg — %s uppdaterade, %s redan aktuella',
        updated or 'inga', skipped or 'inga')
