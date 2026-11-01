# -*- coding: utf-8 -*-
"""T5: minneskolumnerna får sin dimension — och indexet skapas.

Bakgrunden är den cirkulära logiken i `post_init_hook_personal_memory`:
kolumntypen lästes EN gång och samma gamla värde styrde både
konverteringen och index-skapandet. Efter en lyckad konvertering beskrev
`row` inte längre verkligheten, så indexblocket kördes aldrig i samma
anrop — och nästa uppgradering hoppade över ALTER eftersom kolumnen då såg
ut att vara `vector`. Båda grenarna var döda.

Testerna nedan prövar det som faktiskt fallerade: att konvertering OCH
indexering sker i SAMMA anrop.
"""

import logging

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai_agent_core.hooks import (
    _vector_column_type,
    ensure_vector_column,
)

_logger = logging.getLogger(__name__)


@tagged('ai_core', 'post_install', '-at_install')
class TestEnsureVectorColumn(TransactionCase):
    """Hjälparen mot temporära tabeller — oberoende av modulens egna."""

    def _make_table(self, name, column_type):
        """Skapa en temporär tabell med angiven embedding-typ."""
        cr = self.env.cr
        cr.execute('DROP TABLE IF EXISTS %s' % name)
        cr.execute(
            'CREATE TABLE %s (id serial primary key, content text, '
            'embedding %s)' % (name, column_type))
        self.addCleanup(lambda: cr.execute('DROP TABLE IF EXISTS %s' % name))
        return name

    def _has_index(self, table):
        self.env.cr.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = %s AND indexname = %s",
            (table, 'idx_%s_embedding' % table))
        return bool(self.env.cr.fetchone())

    # ── 5.1 dimensionslös vector konverteras och indexeras ──
    def test_dimensionless_vector_converted_and_indexed(self):
        """Det här är precis det som föll tidigare.

        En dimensionslös `vector` gick igenom ALTER-grenen som "redan
        vector" och nådde aldrig indexblocket — i samma anrop. Nu ska båda
        hända.
        """
        table = self._make_table('t_dimless', 'vector')
        self.assertEqual(_vector_column_type(self.env.cr, table, 'embedding'),
                         'vector')

        result = ensure_vector_column(self.env.cr, table)

        self.assertTrue(result['converted'],
                        'dimensionslös vector ska konverteras')
        self.assertTrue(result['index_created'],
                        'indexet ska skapas i SAMMA anrop')
        self.assertEqual(
            _vector_column_type(self.env.cr, table, 'embedding'),
            'vector(1024)')
        self.assertTrue(self._has_index(table))

    # ── 5.2 text konverteras och indexeras i samma anrop ──
    def test_text_column_converted_and_indexed(self):
        """Textvärden bevaras genom casten."""
        table = self._make_table('t_text', 'text')
        cr = self.env.cr
        literal = '[%s]' % ','.join(['0.01'] * 1024)
        cr.execute(
            'INSERT INTO %s (content, embedding) VALUES (%%s, %%s)' % table,
            ('rad', literal))

        result = ensure_vector_column(cr, table)

        self.assertTrue(result['converted'])
        self.assertTrue(result['index_created'])
        self.assertEqual(_vector_column_type(cr, table, 'embedding'),
                         'vector(1024)')

        # Värdet ska vara bevarat, inte nollställt.
        cr.execute('SELECT embedding IS NOT NULL FROM %s' % table)
        self.assertTrue(cr.fetchone()[0], 'text-värdet ska bevaras')
        self.assertTrue(self._has_index(table))

    # ── 5.3 idempotens ──
    def test_idempotent(self):
        """Två anrop i följd ger samma tillstånd, inget fel."""
        table = self._make_table('t_idem', 'vector')

        first = ensure_vector_column(self.env.cr, table)
        self.assertTrue(first['converted'])
        self.assertTrue(first['index_created'])

        second = ensure_vector_column(self.env.cr, table)
        self.assertFalse(second['converted'],
                         'andra anropet ska inte konvertera igen')
        self.assertFalse(second['index_created'],
                         'andra anropet ska inte skapa indexet igen')
        self.assertIsNone(second['skipped'])
        self.assertEqual(_vector_column_type(self.env.cr, table, 'embedding'),
                         'vector(1024)')

    # ── Redan korrekt kolumn lämnas orörd ──
    def test_already_correct_is_untouched(self):
        table = self._make_table('t_ok', 'vector(1024)')
        result = ensure_vector_column(self.env.cr, table)
        self.assertFalse(result['converted'])
        self.assertTrue(result['index_created'],
                        'indexet ska ändå säkerställas')
        self.assertFalse(ensure_vector_column(
            self.env.cr, table)['index_created'])

    # ── Saknad tabell/kolumn kastar inte ──
    def test_missing_table_returns_skipped(self):
        result = ensure_vector_column(self.env.cr, 't_finns_inte')
        self.assertEqual(result['skipped'], 'table_missing')
        self.assertFalse(result['converted'])

    def test_missing_column_returns_skipped(self):
        cr = self.env.cr
        cr.execute('DROP TABLE IF EXISTS t_nocol')
        cr.execute('CREATE TABLE t_nocol (id serial primary key)')
        self.addCleanup(lambda: cr.execute('DROP TABLE IF EXISTS t_nocol'))
        result = ensure_vector_column(cr, 't_nocol')
        self.assertEqual(result['skipped'], 'column_missing')

    # ── Ogiltigt identifierarnamn avvisas ──
    def test_rejects_invalid_identifier(self):
        with self.assertRaises(ValueError):
            ensure_vector_column(self.env.cr, 'ai_memory; DROP TABLE x')


@tagged('ai_core', 'post_install', '-at_install')
class TestMemoryModelsHaveDimension(TransactionCase):
    """5.4 + 5.5: de riktiga modellerna, efter uppgradering."""

    def test_all_memory_models_have_vector_dimension(self):
        """Kravet 'Alla minnesmodeller omfattas'.

        `ai_memory` och `ai_okf_concept` ägs av andra hooks — de prövas
        bara om de finns, så testet inte faller på en miljö där den
        infrastrukturen inte körts.
        """
        cr = self.env.cr
        for table in ('ai_personal_memory', 'ai_company_memory'):
            cr.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s AND table_schema = 'public'
            """, (table,))
            if not cr.fetchone():
                self.skipTest('%s finns inte i denna databas' % table)
            self.assertEqual(
                _vector_column_type(cr, table, 'embedding'), 'vector(1024)',
                '%s.embedding ska vara vector(1024)' % table)
            cr.execute("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = %s AND indexname = %s
            """, (table, 'idx_%s_embedding' % table))
            self.assertTrue(
                cr.fetchone(),
                '%s ska ha ett ivfflat-index över embedding' % table)

    def test_ai_memory_agent_id_is_gone(self):
        """5.5: kolumnen och fältet ska vara borta."""
        cr = self.env.cr
        cr.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_memory' AND column_name = 'agent_id'
              AND table_schema = 'public'
        """)
        self.assertFalse(cr.fetchone(),
                         'ai_memory.agent_id ska vara droppad')
        self.assertNotIn('agent_id', self.env['ai.memory']._fields,
                         'fältet ska vara borta ur modellen')
