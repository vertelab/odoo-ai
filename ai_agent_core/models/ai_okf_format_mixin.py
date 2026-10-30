# -*- coding: utf-8 -*-
"""ai.okf.format.mixin — OKF:s FORMAT, utan indexeringskontraktet.

VARFÖR DENNA MIXIN FINNS (ai-memory-okf-format, 2026-10-30)
----------------------------------------------------------
`ai.okf.mixin` bar två saker som såg ut som en:

    okf_body · okf_summary · okf_tags · okf_links   ← FORMATET
    okf_dirty · okf_indexed_at + hookar             ← INDEXERINGEN

`memory`-specen förbjuder den andra gruppen på `ai.memory`:

    ai.memory SHALL INTE vara en OKF-konsument. Modellen SHALL INTE bära
    okf_dirty, okf_indexed_at eller artifact_type_id.

Men den första gruppen är precis vad en markdown-fil ÄR. Att neka
`ai.memory` hela mixinen för att flaggorna är förbjudna kastar ut
formatet med badvattnet.

    ai.okf.format.mixin          (abstrakt, _auto=False)
    ├── okf_body, okf_summary, okf_links
    │
    ├──► ai.okf.mixin            (+ okf_dirty, okf_indexed_at, hookar)
    │    ├── ai.personal.memory
    │    └── ai.company.memory
    │
    └──► ai.memory                (format, ingen indexering)

VINSTEN ÄR STRUKTURELL, INTE KOSMETISK
--------------------------------------
Före delningen var förbudet en kommentar i `ai_memory.py` som en framtida
utvecklare måste läsa och förstå. Efter delningen kan `ai.memory` **inte**
sätta en OKF-flagga — fältet finns inte. Spec-regeln blir omöjlig att
bryta av misstag.

OM okf_tags
-----------
`okf_tags` är INTE deklarerat här. En many2many på en ABSTRAKT modell ger
samma relationstabell för alla ärvande modeller:

    TypeError: Many2many fields ai.company.memory.okf_tags and
               ai.personal.memory.okf_tags use the same table

Varje konkret modell deklarerar sitt eget:

    okf_tags = fields.Many2many(
        'ai.okf.tag', '<modell>_okf_tag_rel', 'res_id', 'tag_id')
"""

from odoo import models, fields

SUMMARY_MAX_CHARS_PARAM = 'ai_agent_core.okf_summary_max_chars'
SUMMARY_MAX_CHARS_DEFAULT = 2000


class AIOkfFormatMixin(models.AbstractModel):
    """OKF:s fältkontrakt — utan flaggorna som gör det till en konsument."""

    _name = 'ai.okf.format.mixin'
    _description = 'OKF Format Mixin — body, summary, links'
    _auto = False

    # Mappningen mot OKF:s markdown-fil:
    #   frontmatter:  title, description (okf_summary), tags (okf_tags)
    #   body:         okf_body  ("everything after the frontmatter")
    #   länkar:       okf_links (OKF §6.1 — koncept till koncept)
    okf_body = fields.Text(
        'OKF Body',
        help='OKF:ns body — modellens hela text (name, description, notes, '
             'arch_db ...). Materialet, inte det som söks. Fylls av '
             'modellens _okf_body_source().')
    okf_summary = fields.Text(
        'OKF Summary',
        help='OKF:ns description — det tunna konceptet som embeddas och '
             'söks. Hålls inom den konfigurerade gränsen '
             '(%s, default %s).' % (SUMMARY_MAX_CHARS_PARAM,
                                    SUMMARY_MAX_CHARS_DEFAULT))
    okf_links = fields.Json(
        'OKF Links', default=list,
        help='OKF:ns [[länkar]] — referenser till andra poster, som '
             '"res.partner,10". Otypade (OKF §6.1): relationen bor i '
             'okf_body, inte i länken. Trasiga länkar är tillåtna — målet '
             'kanske ännu inte har ett koncept.')

    # ==================================================================
    # Källmetoderna (_okf_body_source, _okf_summary_source) deklareras av
    # ai.okf.mixin, som har en generisk default (alla HTML/Text-fält +
    # name). Format-mixinen läser bara fälten — den äger ingen källa.
    # ==================================================================
