# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.215: tvinga om data-definierad verktygskod.

BAKGRUND
--------
data/youtube_tools.xml ligger i en <odoo noupdate="1">-fil. Det betyder att
ir.model.data-raderna får noupdate=True och att Odoo HOPPAR ÖVER dem vid
--update. _seed_builtin_tools() (models/ai_tool.py) skapar bara SAKNADE
verktyg — för ett verktyg som redan finns rör den builtin_name, nats_subject
och nats_skills, men ALDRIG 'code' eller 'description'.

Följden är att ändringar i youtube_tools.xml bara slår igenom vid FÖRSTA
installationen. På ett uppgraderat system (som ledningssystem) fortsätter den
gamla koden att köras — fixen blir verkningslös.

VAD DENNA MIGRATION GÖR
-----------------------
Läser de fyra YouTube-verktygens 'code' och 'description' ur samma XML-fil
som installationen använder, och skriver över raderna i ai_tool. Källan är
alltså fortfarande filen — migrationen duplicerar inte koden, den tvingar
bara fram den om-läsning som noupdate annars förhindrar.

Riktat: bara de fyra verktygen i YOUTUBE_TOOLS rörs. Övriga noupdate-verktyg
(providers, cron, browser_tools, graph_base …) lämnas orörda — deras
noupdate-status skyddar användarnas egna ändringar och är avsiktlig.

Idempotent: att köra migrationen två gånger ger samma resultat.
"""

import logging
import os
import xml.etree.ElementTree as ET

_logger = logging.getLogger(__name__)

# xmlid → (tool-namn, förväntat fält). Endast dessa rörs.
YOUTUBE_TOOLS = {
    'tool_youtube_get_transcript': 'youtube_get_transcript',
    'tool_youtube_search': 'youtube_search',
    'tool_youtube_channel': 'youtube_channel',
    'tool_youtube_playlist': 'youtube_playlist',
}


def _read_tool_data(module_dir):
    """Läs code + description för YouTube-verktygen ur youtube_tools.xml.

    Returnerar {tool_namn: {'code': ..., 'description': ...}}.
    """
    path = os.path.join(module_dir, 'data', 'youtube_tools.xml')
    if not os.path.exists(path):
        _logger.warning(
            "Migration 18.0.1.215: hittade inte %s — hoppar över", path)
        return {}

    data = {}
    tree = ET.parse(path)
    for record in tree.getroot().iter('record'):
        xmlid = record.get('id')
        if xmlid not in YOUTUBE_TOOLS:
            continue
        fields = {}
        for field in record.findall('field'):
            if field.get('name') in ('code', 'description'):
                fields[field.get('name')] = field.text or ''
        if 'code' in fields:
            data[YOUTUBE_TOOLS[xmlid]] = fields
    return data


def migrate(cr, version):
    _logger.info(
        "Running migration 18.0.1.215: tvinga om data-definierad verktygskod")

    # __file__ = <modul>/migrations/18.0.1.215/post-migrate.py → tre nivåer upp
    module_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data = _read_tool_data(module_dir)
    if not data:
        _logger.warning("Migration 18.0.1.215: inga verktyg att uppdatera")
        return

    updated = 0
    for tool_name, fields in data.items():
        code = fields.get('code')
        description = fields.get('description')
        if code is None:
            continue
        # Matcha på name (inte xmlid) — _seed_builtin_tools skapar posten med
        # name = builtin_name, och xmlid-bindningen kan saknas på äldre rader.
        cr.execute("""
            UPDATE ai_tool
               SET code = %s,
                   description = COALESCE(%s, description),
                   write_date = NOW()
             WHERE name = %s
        """, (code, description, tool_name))
        if cr.rowcount:
            updated += cr.rowcount
            _logger.info(
                "Migration 18.0.1.215: %s uppdaterad (%d tecken kod)",
                tool_name, len(code))
        else:
            _logger.info(
                "Migration 18.0.1.215: %s finns inte i denna databas — hoppar över",
                tool_name)

    _logger.info(
        "Migration 18.0.1.215: %d verktyg tvingades om", updated)
