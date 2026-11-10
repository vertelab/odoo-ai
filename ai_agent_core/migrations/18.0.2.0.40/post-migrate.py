# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.40: städa namn med ogiltiga tecken.

VARFÖR (fix-tool-name-sanitization): ett verktygsnamn som byggs ur ett
agentnamn ärver godtyckliga tecken. Agenten `SEO & GEO-specialist` gav
`call_specialist_seo_&_geo_specialist`, som opencode-go nekade med
`[InvalidParameter] invalid function name format` — och hela webbchatten
svarade "Anslutningen till AI-servern bröts".

Saniteringen (`core/tools.py::_sanitize_tool_name`) gör namnen **anropbara**
även utan städning (`seo_geo_specialist`), men då står det `seo_geo_specialist`
i verktygslistan och `SEO & GEO-specialist` i UI:t — två namn för samma sak.
Den här migrationen gör dem läsbara.

IDEMPOTENT: byter bara om det gamla namnet finns kvar. Körs den två gånger
hittar den inget att byta.

BARA FYRA POSTER: poster vars enda "fel" är `ä`/`å` (t.ex. `Allmän assistent`)
klarar sig via translittereringen och rörs inte — att byta dem hade varit
ändring utan vinst.
"""

import logging

_logger = logging.getLogger(__name__)

# (modell, id, gammalt namn, nytt namn)
RENAMES = [
    ('ai.agent', 10, 'Mail → Partner', 'Mail till Partner'),
    ('ai.agent', 772, 'SEO & GEO-specialist', 'SEO och GEO-specialist'),
    ('ai.coworker', 22, 'Enterprise Architect — User Stories',
     'Enterprise Architect - User Stories'),
    ('ai.coworker', 23, 'Enterprise Architect — Projekt',
     'Enterprise Architect - Projekt'),
]


def migrate(cr, version):
    if not version:
        return

    changed = 0
    for model, rec_id, old, new in RENAMES:
        table = model.replace('.', '_')
        # Byt bara om posten finns OCH bär det gamla namnet. Det gör
        # körningen idempotent och skyddar mot att någon redan döpt om
        # posten till något annat.
        cr.execute(
            'UPDATE %s SET name = %%s WHERE id = %%s AND name = %%s'
            % table,
            (new, rec_id, old))
        if cr.rowcount:
            changed += 1
            _logger.info(
                'Post-migration 18.0.2.0.40: %s %s: %r → %r',
                model, rec_id, old, new)
        else:
            _logger.info(
                'Post-migration 18.0.2.0.40: %s %s hade inte namnet %r — '
                'oförändrad', model, rec_id, old)

    _logger.info(
        'Post-migration 18.0.2.0.40: %s av %s namn ändrade',
        changed, len(RENAMES))
