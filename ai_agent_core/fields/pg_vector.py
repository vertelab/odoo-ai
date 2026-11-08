# -*- coding: utf-8 -*-
"""Minimal PgVector-fält för OKF — ingen import från ai_agent_pgvector.

Skapar en riktig pgvector-kolumn (vector(n)) med ivfflat/hnsw-index.
Kräver att postgres-extensionen 'vector' är aktiverad i databasen
(hanteras i migration/post_init via CREATE EXTENSION IF NOT EXISTS).
"""

import logging

from odoo import fields, tools

_logger = logging.getLogger(__name__)

try:
    # `pgvector.utils` finns INTE i pgvector 0.5.x — modulen heter
    # `pgvector.vector`. Importen pekade på den gamla sökvägen, så den
    # misslyckades alltid och `Vector` blev None även med paketet installerat.
    # Konsekvensen var tyst: `convert_to_column` returnerade None och varje
    # embedding blev NULL i databasen.
    #
    # OBS: `Vector._to_db(value)` tar ETT argument — ingen dimension.
    # `pgvector.psycopg2.vector.Vector._to_db` är en bunden metod med samma
    # signatur, så den går inte heller att anropa med (value, dim).
    from pgvector.vector import Vector
except ImportError:
    Vector = None
    _logger.warning(
        'The pgvector python package is missing — vectors are written as literals. Install with: pip install pgvector (and add it to external_dependencies).')


def register_vector_adapter(cr):
    """Registrera en psycopg2-adapter så att `vector` läses som en lista.

    Utan den här får Odoo en RÅ STRÄNG från databasen. Odoo 18 lägger
    DB-värdet direkt i cachen (`api.py:1260 insert_missing`) utan att gå
    via `convert_to_cache`, så en oregistrerad typ blir en sträng i cachen.
    Konsekvensen: `len(value)` är 4097 i stället för 1024, och Odoos
    `val != value`-jämförelse ser skillnad vid varje skrivning.

    Anropas från post_init och migration. Idempotent — psycopg2 tillåter
    att samma typ registreras om.
    """
    if Vector is None:
        return False
    try:
        from psycopg2.extensions import new_type, register_type
        cr.execute("SELECT oid FROM pg_type WHERE typname = 'vector'")
        row = cr.fetchone()
        if not row:
            return False
        oid = row[0]
        VECTOR = new_type((oid,), 'VECTOR', lambda value, cur: Vector._from_db(value))
        register_type(VECTOR)
        return True
    except Exception as e:  # noqa: BLE001 — får ej stoppa uppgraderingen
        _logger.warning('Kunde inte registrera vector-adaptern: %s', e)
        return False


class PgVector(fields.Field):
    """pgvector column type for OKF concepts (dimension 1024)."""

    type = 'pgvector'
    column_type = ('vector', 'vector')

    _slots = {
        'dimension': None,
    }

    def __init__(self, string=None, dimension=None, **kwargs):
        super().__init__(string=string, **kwargs)
        self.dimension = dimension

    def convert_to_column(self, value, record, values=None, validate=True):
        """Konvertera Python-värde (sträng-vektorliteral) till databas-format.

        Tre former förekommer i praktiken:
          - sträng "[0.1,0.2,...]" (vad providern returnerar)
          - list/tuple av tal
          - numpy-array (när anroparen gjort `np.array(...)`)

        numpy-fallet kräver `tolist()`: `Vector._to_db` itererar värdet och
        numpy skapar då en array per element, vilket får `if not value` att
        kasta "truth value of an array is ambiguous" — felet som tystade
        backfillen (test_personal_memory_embedding.TestBackfill).
        """
        if value is None:
            return None
        # value är en pgvector-literal-sträng "[0.1,0.2,...]" eller list
        if isinstance(value, str):
            value = value.strip()
            if value.startswith('[') and value.endswith(']'):
                value = value[1:-1].split(',')
        elif hasattr(value, 'tolist'):
            # numpy-array (eller annan array-liknande) → vanlig lista
            value = value.tolist()
        if Vector is not None:
            return Vector._to_db(value)
        # UTAN pgvector-paketet: bygg literalen själv.
        #
        # Den tidigare koden returnerade None här, vilket gjorde att VARJE
        # skrivning av en embedding tystnade: cachen höll 1024 flyttal,
        # `write()` gick igenom, och kolumnen blev NULL i databasen. Felet
        # syntes aldrig — sökningen hittade bara ingenting.
        return '[' + ','.join(str(float(x)) for x in value) + ']'

    def convert_to_cache(self, value, record, validate=True):
        """Konvertera DB-värde (vector-typ) till cache (list).

        Cachen MÅSTE hålla en vanlig lista. Odoo jämför cache-värden med
        `val != value` (fields.py:1212, `get_records_different_from`), och
        en numpy-array ger då "truth value of an array with more than one
        element is ambiguous" — skrivningen faller innan den nått databasen.
        Det var felet som tystade backfillen: `convert_to_column` var rätt,
        men cachen kastade först.
        """
        if value is None:
            return None
        if Vector is None:
            # Utan pgvector-paketet: parsa literalen själv till en lista.
            # Att returnera strängen hade gjort att Odoos
            # `val != value`-jämförelse såg skillnad varje gång.
            if isinstance(value, str):
                inner = value.strip().lstrip('[').rstrip(']')
                if not inner:
                    return None
                return [float(x) for x in inner.split(',')]
            return value
        if isinstance(value, str):
            # `Vector._from_db` ger en lista i pgvector 0.5.x, men en
            # numpy-array i äldre versioner. Cachen MÅSTE hålla en vanlig
            # lista: Odoo jämför med `val != value`, och en ndarray kastar
            # "truth value of an array is ambiguous" vid nästa skrivning.
            parsed = Vector._from_db(value)
            return parsed.tolist() if hasattr(parsed, 'tolist') else parsed
        if hasattr(value, 'tolist'):
            return value.tolist()
        return value

    def create_column(self, cr, table, column, **kwargs):
        """Skapa vector-kolumnen. Kräver att 'vector'-extensionen finns."""
        dim_spec = '(%d)' % self.dimension if self.dimension else ''
        cr.execute(
            "ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s vector%s"
            % (table, column, dim_spec))
        tools.set_column_type(cr, table, column, 'vector%s' % dim_spec)

    def create_index(self, cr, table, column, index_name, dimensions,
                     force=False):
        """Skapa ivfflat-index om det inte finns. Idempotent."""
        if force:
            cr.execute('DROP INDEX IF EXISTS %s' % index_name)
        else:
            cr.execute(
                'SELECT 1 FROM pg_indexes WHERE indexname = %s',
                (index_name,))
            if cr.fetchone():
                return
        try:
            cr.execute("""
                CREATE INDEX %s ON %s
                USING ivfflat (%s vector_cosine_ops) WITH (lists = 100)
            """ % (index_name, table, column))
            _logger.info('Created ivfflat index %s', index_name)
        except Exception as e:
            _logger.warning('Could not create ivfflat index %s: %s',
                            index_name, e)
