# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.241: städa OKF-uppladdningskön (1391 felposter).

Bakgrund
--------
`ai_okf_upload` hade 1391 poster i `state='error'` i `ledningssystem`.
Tre separata buggar hade fyllt kön:

  1. `_has_capability` letade en ai.skill/ai.tool med NAMNET 'image',
     'pdf' eller 'docx'. Sådana skills finns inte, och `coworker_id` är
     NULL för alla web-UI-uppladdningar → metoden returnerade alltid False.
     736 poster: "Förmågan \\"image\\" saknas på coworkerns agenter".

  2. `tesseract`-binären saknades i containern (pytesseract är bara ett
     omslag). OCR-anropet kastade FileNotFoundError som svaldes av en bred
     except. Fixat i Salt: odoo/18.sls installerar tesseract-ocr,
     tesseract-ocr-swe och poppler-utils.

  3. `ir.attachment.create` skickade ALLT som inte var text/* till kön —
     356 SVG-ikoner, 58 JS-assets, zip-arkiv m.m. De kunde aldrig bli
     kunskap. Fixat med `_is_knowledge_candidate` (MIME-grind).

Vad migrationen gör
-------------------
**Raderar** poster som bevisligen aldrig kan bli kunskap, och **återköar**
resten så att den nya normaliseraren får ett försök.

Raderas (hopplösa — felet ligger i filen, inte i koden):
  - "Binärfil indexeras inte" — zip, rar, octet-stream
  - "Bilagan saknas." — attachment_id pekar på en raderad bilaga
  - "Filen är för stor" — över 50 MB-gränsen

Återköas (kan nu lyckas):
  - allt annat i state='error' → state='queued', error_message nollas

Varför radera just dessa
------------------------
ADD-only gäller koncept, inte kö-poster. En kö-post är en arbetsorder, inte
kunskap. En order om en zip-fil är meningslös — den kan inte utföras. Att
behålla 1391 döda arbetsordrar gör dashboarden obrukbar och skymmer de fel
som faktiskt betyder något.

Vad som INTE påverkas
---------------------
- `state='done'` — 306 färdiga uppladdningar och deras koncept, orörda.
- `ai.okf.concept` — inga koncept raderas.
- Bilagorna själva (`ir.attachment`) — orörda.
"""

import logging

_logger = logging.getLogger(__name__)

# Felmeddelanden vars post aldrig kan bli kunskap — filen är problemet.
_HOPELESS_PREFIXES = (
    'Binärfil indexeras inte',
    'Bilagan saknas.',
    'Filen är för stor',
)


def migrate(cr, version):
    cr.execute("""
        SELECT count(*) FROM ai_okf_upload WHERE state = 'error'
    """)
    before = cr.fetchone()[0]
    if not before:
        _logger.info('18.0.1.241: inga felposter att städa')
        return

    # 1. Radera hopplösa poster (filen kan aldrig bli kunskap)
    cr.execute("""
        DELETE FROM ai_okf_upload
        WHERE state = 'error'
          AND (
            error_message LIKE %s
            OR error_message LIKE %s
            OR error_message LIKE %s
          )
    """, tuple(p + '%' for p in _HOPELESS_PREFIXES))
    removed = cr.rowcount

    # 2. Återköa resten — den nya normaliseraren får ett försök
    cr.execute("""
        UPDATE ai_okf_upload
        SET state = 'queued',
            error_message = NULL,
            retry_count = retry_count + 1
        WHERE state = 'error'
    """)
    requeued = cr.rowcount

    _logger.info(
        '18.0.1.241: OKF-kön städad — %d raderade (hopplösa), '
        '%d återköade (kan nu normaliseras). Före: %d felposter.',
        removed, requeued, before)
