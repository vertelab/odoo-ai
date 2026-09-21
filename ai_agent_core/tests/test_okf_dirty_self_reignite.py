# -*- coding: utf-8 -*-
"""Regressionstest: `ai.memory` självåtertände `okf_dirty` (fynd 2026-09-21).

VARFÖR: `ai.memory.write()` satte `okf_dirty = True` VILLKORSLÖST — även när
anroparen uttryckligen ville RENSA den. Cronen
`_okf_cron_index_dirty_memories()` rensar med `mem.write({'okf_dirty': False})`,
vilket gick rakt in i samma hook som satte flaggan igen. Resultatet: varje
cron-varv (5 min) indexerade om samma post och skapade en ny OKF-version.

Mätt i drift innan fixen: `concept_key = ai.memory,257` hade 38 versioner
(v1 11:36 → v38 15:06), sex andra nycklar 28–29 versioner — alla med
identiskt innehåll ("hello test").

Dessa tester bevisar att:
  1. en explicit rensning (`okf_dirty=False`) respekteras,
  2. en innehållsändring fortfarande sätter flaggan,
  3. cronen är idempotent: andra körningen skapar ingen ny version,
  4. `okf_indexed_at` finns på `ai.memory` (den saknades — cronen skrev
     till ett fält som bara fanns på mixin, och felet svaldes av try/except).
"""

from odoo.tests import common, tagged


@tagged('okf', 'memory', 'post_install', '-at_install')
class TestOkfDirtySelfReignite(common.TransactionCase):
    """Fynd 2026-09-21: flaggan får inte tändas av sin egen rensning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Memory = cls.env['ai.memory']
        cls.Concept = cls.env['ai.okf.concept']

    def _make_memory(self, content='hello test'):
        """Skapa en post och markera den dirty.

        OBS: `ai.memory` har ingen `create()`-hook och `okf_dirty` default
        är `False` — en ny post är alltså INTE dirty av sig själv. Det
        verkliga flödet går via `write()` (t.ex. när innehåll/vektor
        läggs på), så testet gör samma sak.
        """
        mem = self.Memory.create({
            'name': 'test-memory',
            'content': content,
            'memory_type': 'text',
        })
        mem.write({'tags': 'trigger'})  # tänder flaggan via write()-hooken
        mem.invalidate_recordset(['okf_dirty'])
        return mem

    # ── 1: explicit rensning respekteras ──

    def test_explicit_false_is_respected(self):
        """Kärnan i buggen: write({'okf_dirty': False}) fick INTE tända igen."""
        mem = self._make_memory()
        self.assertTrue(mem.okf_dirty, 'posten ska vara dirty efter write')

        mem.write({'okf_dirty': False})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(
            mem.okf_dirty,
            'explicit rensning ska respekteras — annars självåtertänder '
            'flaggan och cronen skapar en ny version var 5:e minut')

    # ── 2: innehållsändring sätter fortfarande flaggan ──

    def test_content_write_still_sets_dirty(self):
        """Fixen får inte göra hooken tandlös."""
        mem = self._make_memory()
        mem.write({'okf_dirty': False})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty)

        # En vanlig fältändring (utan att nämna okf_dirty) ska tända flaggan.
        mem.write({'tags': 'faktura, e-post'})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertTrue(
            mem.okf_dirty,
            'en skrivning som inte nämner okf_dirty ska sätta flaggan')

    # ── 3: cronen är idempotent ──

    def test_cron_is_idempotent(self):
        """Andra cron-körningen ska inte skapa en ny version."""
        mem = self._make_memory('idempotens-test')
        key = 'ai.memory,%s' % mem.id

        # Första körningen: indexerar och rensar.
        self.Memory._okf_cron_index_dirty_memories(batch_size=50)
        mem.invalidate_recordset(['okf_dirty', 'okf_indexed_at'])
        self.assertFalse(mem.okf_dirty, 'cronen ska ha rensat flaggan')
        self.assertTrue(mem.okf_indexed_at, 'cronen ska ha satt tidsstämpeln')

        first = self.Concept.search(
            [('concept_key', '=', key)], order='version desc')
        self.assertTrue(first, 'första körningen ska ha skapat ett koncept')
        first_version = first[0].version

        # Andra körningen: flaggan är ren, inget ska hända.
        self.Memory._okf_cron_index_dirty_memories(batch_size=50)
        second = self.Concept.search(
            [('concept_key', '=', key)], order='version desc')
        self.assertEqual(
            second[0].version, first_version,
            'oförändrad post får inte skapa en ny version — det var '
            'versionsstormen (38 versioner i drift)')

    # ── 4: okf_indexed_at finns på ai.memory ──

    def test_okf_indexed_at_exists_on_ai_memory(self):
        """Fältet saknades — cronen skrev till ett fält som inte fanns."""
        self.assertIn('okf_indexed_at', self.Memory._fields)
