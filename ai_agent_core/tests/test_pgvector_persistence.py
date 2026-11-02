# -*- coding: utf-8 -*-
"""PgVector-fältet måste PERSISTERA en vektor — inte tyst skriva NULL.

Bakgrund: `convert_to_column` returnerade `None` när python-paketet
`pgvector` saknades. Cachen höll 1024 flyttal, `write()` gick igenom utan
fel, och kolumnen blev NULL i databasen. Ingen söktrad hittades någonsin,
och inget fel loggades. Fältet används av fem modeller (personal/company
memory, ai.memory, ai.memory.chunk, ai.okf.concept).
"""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai_agent_core.fields import pg_vector


@tagged('ai_core', 'post_install', '-at_install')
class TestPgVectorPersistsWithoutPackage(TransactionCase):
    """Utan pgvector-paketet ska literalen byggas ändå — inte bli None."""

    def setUp(self):
        super().setUp()
        self.Memory = self.env['ai.personal.memory']
        self.field = self.Memory._fields['embedding']

    def _memory(self, content='vektor-probe'):
        return self.Memory.create({
            'user_id': self.env.user.id,
            'content': content,
            'category': 'fact',
            'source': 'manual',
        })

    def test_convert_to_column_never_returns_none_for_a_vector(self):
        """KÄRNFELEt: en giltig vektor får aldrig bli None."""
        with patch.object(pg_vector, 'Vector', None):
            result = self.field.convert_to_column([0.5] * 1024, self.Memory)
        self.assertIsNotNone(
            result,
            'convert_to_column gav None — skrivningen tystnar och '
            'kolumnen blir NULL')
        self.assertIn('0.5', result)
        self.assertTrue(result.startswith('[') and result.endswith(']'))

    def test_write_reaches_the_database(self):
        """Hela vägen: write() → DB. Det var här felet var osynligt."""
        rec = self._memory()
        with patch.object(pg_vector, 'Vector', None):
            rec.write({'embedding': [0.25] * 1024})
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT embedding IS NULL FROM ai_personal_memory WHERE id = %s',
            (rec.id,))
        is_null = self.env.cr.fetchone()[0]
        self.assertFalse(
            is_null,
            'embedding är NULL i databasen efter write() — vektorn tappades')

    def test_read_back_gives_a_list_of_floats(self):
        """convert_to_cache måste ge en lista, inte en sträng.

        Testet prövar `convert_to_cache` DIREKT, inte via en omläsning:
        i Odoo 18 går DB-värdet rakt in i cachen utan att passera
        `convert_to_cache` (cachen fylls av `_read`), så en omläsning
        bevisar ingenting om konverteringen. Det var därför testet föll på
        en sträng — den kom från `convert_to_column`, inte från cachen.
        """
        rec = self._memory()
        with patch.object(pg_vector, 'Vector', None):
            rec.write({'embedding': [0.125] * 1024})
        self.env.flush_all()
        rec.invalidate_recordset()
        self.env.cr.execute(
            'SELECT embedding::text FROM ai_personal_memory WHERE id = %s',
            (rec.id,))
        db_value = self.env.cr.fetchone()[0]
        self.assertTrue(db_value, 'embedding är tom i databasen')
        value = self.field.convert_to_cache(db_value, self.Memory)
        self.assertTrue(value, 'embedding är tom efter omläsning')
        self.assertIsInstance(
            value, list,
            'convert_to_cache gav %s, inte en lista — Odoos '
            'cache-jämförelse kastar då vid nästa skrivning'
            % type(value).__name__)
        self.assertEqual(len(value), 1024)
        self.assertAlmostEqual(float(value[0]), 0.125, places=6)

    def test_dimension_is_enforced_by_the_column(self):
        """Fel dimension ska avvisas av databasen, inte tyst accepteras.

        `assertRaises` måste omsluta BÅDE write och flush: write() lägger
        bara värdet i cachen, och felet kommer först när det når kolumnen.
        Låg flush utanför blocket kastade felet utanför testets kontroll —
        det rapporterades som ett oväntat ERROR i stället för ett godkänt
        undantag.
        """
        rec = self._memory()
        with patch.object(pg_vector, 'Vector', None):
            with self.assertRaises(Exception):
                rec.write({'embedding': [0.1] * 3})
                self.env.flush_all()
            # Felet lämnar en trasig transaktion. Utan rollback till en
            # savepoint följer den med resten av testsviten och fäller
            # efterföljande tester med "current transaction is aborted".
            self.env.cr.rollback()

    def test_none_still_means_no_vector(self):
        """None ska fortfarande vara NULL — guarden får inte bli en påtvingad vektor."""
        with patch.object(pg_vector, 'Vector', None):
            self.assertIsNone(self.field.convert_to_column(None, self.Memory))
