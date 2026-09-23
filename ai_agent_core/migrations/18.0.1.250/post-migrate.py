# -*- coding: utf-8 -*-
"""Migrate to 18.0.1.250: pensionera ai.memory som OKF-konsument (okf-mixin F2).

Bakgrund
--------
`ai.memory` är agentens RAG-kapacitet: vektoriserade PDF:er, `faiss_search`,
`rag_memory_ids`. Det är material en agent ARBETAR med — inte kunskap som ska
bli OKF-koncept.

Modellen bar ändå `okf_dirty` + `okf_indexed_at` + `artifact_type_id` och
indexerades till `ai.okf.concept` av både en cron och en dashboard-åtgärd.
Konsekvensen: en uppladdad PDF blev både FAISS-index OCH kunskapskoncept, och
en dirty-flagga skapade en rad per cron-varv (38 versioner av `ai.memory,257`
innan självåtertändningen fixades 2026-09-21).

Vad migrationen gör
-------------------
**Arkiverar** befintliga `ai.memory,%`-koncept. Ingen rad raderas — ADD-only
gäller, och historiken är den ärliga representationen av vad som hände.

Varför arkivera och inte behålla
--------------------------------
Koncepten skapades ur RAG-material (PDF-chunks), inte ur kuraterad kunskap.
De hör inte i kunskapslagret. Att lämna dem aktiva vore att behålla
kategorifelet i sökningen — `_okf_search` skulle returnera PDF-fragment som
om de vore kunskap.

Varför inte radera
------------------
Samma skäl som alltid i denna kodbas: rader är immutabla och historiken är
bevis. En arkiverad rad injiceras inte och syns inte i sökning, men går att
granska om något visar sig fel.

Vad som INTE påverkas
---------------------
- RAG-kapaciteten (`create_vector`, `faiss_search`, `rag_memory_ids`) — orörd
- `ai.okf.upload`-vägen (ir.attachment → koncept) — orörd
- Koncept från andra källor — orörda
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info('Running migration 18.0.1.250: pensionera ai.memory som '
                 'OKF-konsument')

    # 1. Arkivera ai.memory-koncept (arkivering, inte radering).
    cr.execute("""
        SELECT count(*) FROM ai_okf_concept
        WHERE concept_key LIKE 'ai.memory,%%' AND archived = false
    """)
    to_archive = cr.fetchone()[0]

    if to_archive:
        cr.execute("""
            UPDATE ai_okf_concept SET archived = true
            WHERE concept_key LIKE 'ai.memory,%%' AND archived = false
        """)
        _logger.info(
            'OKF: arkiverade %s ai.memory-koncept (RAG-material hör inte i '
            'kunskapslagret). Inga rader raderade.', to_archive)
    else:
        _logger.info('OKF: inga aktiva ai.memory-koncept att arkivera')

    # 2. Verifiera att kolumnerna är borta (ORM:en tar dem, men fältet kan
    #    ha lämnats kvar av en halv uppgradering).
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_memory'
          AND column_name IN ('okf_dirty', 'okf_indexed_at',
                              'artifact_type_id')
    """)
    left = [row[0] for row in cr.fetchall()]
    if left:
        _logger.warning(
            'OKF: kolumnerna %s finns kvar på ai_memory — fälten är borttagna '
            'ur modellen, så de är oanvända. Kan städas manuellt.', left)

    # 3. Rapportera kvarvarande ai.memory-koncept (superseded-historik).
    cr.execute("""
        SELECT count(*) FROM ai_okf_concept WHERE concept_key LIKE 'ai.memory,%%'
    """)
    _logger.info('OKF: %s ai.memory-koncept totalt (historik, alla arkiverade)',
                 cr.fetchone()[0])
