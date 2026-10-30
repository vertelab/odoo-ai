# -*- coding: utf-8 -*-
"""ai.memory.chunk — chunkar av en ai.memory-artefakt (ai-memory-pgvector).

VARFÖR EN EGEN MODELL OCH INTE ETT FÄLT PÅ FÖRÄLDERN:

En PDF på 300 sidor blir ~900 chunkar à 1 000 tecken ≈ 900 kB. Låg de i
ett Json-fält på `ai.memory` skulle varje `search_read` på modellen
(formulär, listvy, chunk_count-compute) dra hem hela texten. En egen
tabell ger dessutom ett riktigt index och `ondelete='cascade'`.

Chunkarna ersätter det serialiserade FAISS-indexet. `start_index` bevaras
från `RecursiveCharacterTextSplitter(add_start_index=True)` — det är det
enda som låter oss citera tillbaka till rätt ställe i källan.
"""

import logging

from odoo import models, fields

from ..fields.pg_vector import PgVector
from .ai_okf_concept import EMBEDDING_DIM as _OKF_EMBEDDING_DIM

_logger = logging.getLogger(__name__)


class AIMemoryChunk(models.Model):
    _name = 'ai.memory.chunk'
    _description = 'AI Memory Chunk'
    _order = 'sequence asc'

    memory_id = fields.Many2one(
        'ai.memory', string='Memory', required=True,
        ondelete='cascade', index=True,
        help='The artifact this chunk belongs to.')
    sequence = fields.Integer(default=10, help='Ordning inom artefakten.')

    content = fields.Text(
        string='Content', help='Chunkens text (page_content).')

    embedding = PgVector(
        string='Embedding', dimension=_OKF_EMBEDDING_DIM,
        help='pgvector(%d) embedding av content. Kolumnen är vector(%d)-typ '
             '— satt av hooks.py (memory_ensure_search_infrastructure).'
             % (_OKF_EMBEDDING_DIM, _OKF_EMBEDDING_DIM))

    # search_vector — GENERATED COLUMN, skapad av SQL i hooks.py.
    # Är avsiktligt INTE ett ORM-fält: en genererad kolumn ska inte kunna
    # glömmas bort av skrivsidan.

    start_index = fields.Integer(
        help='Position i källtexten där chunken börjar '
             '(RecursiveCharacterTextSplitter add_start_index).')
    metadata = fields.Json(
        string='Metadata',
        help='Källa för chunken: sida, rubrik, ursprungsfil.')
