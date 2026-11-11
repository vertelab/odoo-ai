# -*- coding: utf-8 -*-
"""Tidsresolveraren — svenska uttryck → intervall (okf-temporal-recall).

VARFÖR DET HÄR TESTET FINNS: tolkningen är den enda delen av ändringen som
en användare möter direkt ("förra veckan" måste betyda förra veckan), och
den är ren Python utan `env`. Då finns ingen ursäkt för att inte pröva den
mot ett FAST `now` — tiden injiceras, klockan fryses aldrig.

De tre fällorna som prövas särskilt:

1. `senaste` ≠ `förra`. "Senaste veckan" är rullande 7 dygn; "förra veckan"
   är föregående kalendervecka. Att slå ihop dem ger ett svar som glider
   beroende på vilken veckodag frågan ställs.
2. Veckogränsen räknas i ANVÄNDARENS zon, inte i UTC. En fråga kl 00:30
   lokal tid en måndag ska hamna i rätt vecka.
3. Ordgräns. "veckanlång" får inte ge ett tidsfönster.
"""

from datetime import datetime

from odoo.tests import common, tagged

from ..core import okf_temporal


#: Tisdag 2026-11-10, 14:30 — en vardag mitt i en vecka, så att "förra
#: veckan" och "i veckan" bevisligen skiljer sig.
NOW = datetime(2026, 11, 10, 14, 30)


@tagged('post_install', '-at_install', 'okf', 'temporal')
class TestTemporalResolver(common.TransactionCase):
    """Ren funktion — inga modeller, ingen databas."""

    def _resolve(self, text, now=NOW):
        return okf_temporal.resolve(text, now=now,
                                    tz_name='Europe/Stockholm')

    # ── Grundformen ────────────────────────────────────────────────────

    def test_forra_veckan_is_the_previous_calendar_week(self):
        r = self._resolve('vad skrev Skanska i förra veckan')
        self.assertEqual(r['matched'], 'forra veckan')
        self.assertEqual(r['direction'], 'backward')
        # Måndag 2 nov – söndag 8 nov 2026.
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-02')
        self.assertEqual(r['date_to'].date().isoformat(), '2026-11-08')
        self.assertEqual(r['date_from'].hour, 0)

    def test_i_veckan_is_this_calendar_week(self):
        r = self._resolve('vilka möten har jag i veckan')
        self.assertEqual(r['matched'], 'i veckan')
        self.assertEqual(r['direction'], 'both')
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-09')
        self.assertEqual(r['date_to'].date().isoformat(), '2026-11-15')

    def test_senaste_veckan_rolls_backwards(self):
        """Rullande 7 dygn — INTE samma sak som "förra veckan"."""
        r = self._resolve('mail senaste veckan')
        self.assertEqual(r['matched'], 'senaste veckan')
        self.assertEqual(r['direction'], 'backward')
        self.assertEqual(r['date_to'], r['date_to'])
        span = (r['date_to'] - r['date_from']).days
        self.assertEqual(span, 7)
        self.assertEqual(r['date_to'].date().isoformat(), '2026-11-10')

    def test_senaste_och_forra_are_not_the_same(self):
        """Den viktigaste distinktionen i hela resolvern."""
        senaste = self._resolve('senaste veckan')
        forra = self._resolve('förra veckan')
        self.assertNotEqual(senaste['date_from'], forra['date_from'])

    # ── Riktning ───────────────────────────────────────────────────────

    def test_nasta_vecka_is_forward(self):
        r = self._resolve('mina möten nästa vecka')
        self.assertEqual(r['direction'], 'forward')
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-16')

    def test_idag_is_both(self):
        r = self._resolve('vad hände idag')
        self.assertEqual(r['direction'], 'both')
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-10')
        self.assertEqual(r['date_to'].date().isoformat(), '2026-11-10')

    def test_igar_is_backward(self):
        r = self._resolve('igår')
        self.assertEqual(r['direction'], 'backward')
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-09')

    def test_nasta_manad(self):
        r = self._resolve('nästa månad')
        self.assertEqual(r['direction'], 'forward')
        self.assertEqual(r['date_from'].date().isoformat(), '2026-12-01')
        self.assertEqual(r['date_to'].date().isoformat(), '2026-12-31')

    # ── Tidszon ────────────────────────────────────────────────────────

    def test_week_boundary_uses_the_user_timezone(self):
        """00:30 lokal tid en måndag ska vara DEN veckan, inte förra."""
        monday_early = datetime(2026, 11, 9, 0, 30)
        r = self._resolve('i veckan', now=monday_early)
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-09')
        self.assertEqual(r['date_from'].hour, 0)

    def test_sunday_late_stays_in_the_same_week(self):
        sunday_late = datetime(2026, 11, 8, 23, 30)
        r = self._resolve('i veckan', now=sunday_late)
        self.assertEqual(r['date_from'].date().isoformat(), '2026-11-02')
        self.assertEqual(r['date_to'].date().isoformat(), '2026-11-08')

    # ── Ärlighet ───────────────────────────────────────────────────────

    def test_unknown_expression_sets_no_window(self):
        r = self._resolve('vad hände i förfjol')
        self.assertIsNone(r['date_from'])
        self.assertIsNone(r['date_to'])
        self.assertIsNone(r['direction'])
        self.assertEqual(r['unmatched'], 'i forfjol')

    def test_no_expression_at_all(self):
        r = self._resolve('vilka fakturor är obetalda')
        self.assertIsNone(r['date_from'])
        self.assertIsNone(r['unmatched'])

    def test_word_boundary_does_not_match_inside_a_word(self):
        """'veckanlång' är inte 'veckan'."""
        r = self._resolve('en veckanlång utredning')
        self.assertIsNone(r['date_from'])

    def test_empty_text(self):
        r = self._resolve('')
        self.assertIsNone(r['date_from'])

    # ── Defaultfönstret ────────────────────────────────────────────────

    def test_window_for_backward_default(self):
        start, end, direction = okf_temporal.window_for(
            {'days': 7, 'direction': 'backward'}, now=NOW,
            tz_name='Europe/Stockholm')
        self.assertEqual(direction, 'backward')
        self.assertEqual((end - start).days, 7)
        self.assertEqual(end.date().isoformat(), '2026-11-10')

    def test_window_for_forward_default(self):
        start, end, direction = okf_temporal.window_for(
            {'days': 7, 'direction': 'forward'}, now=NOW,
            tz_name='Europe/Stockholm')
        self.assertEqual(direction, 'forward')
        self.assertEqual((end - start).days, 7)
        self.assertEqual(start.date().isoformat(), '2026-11-10')

    def test_window_for_missing_default_is_none(self):
        self.assertEqual(okf_temporal.window_for(None), (None, None, None))
        self.assertEqual(
            okf_temporal.window_for({'days': 0}), (None, None, None))

    # ── Normalisering ──────────────────────────────────────────────────

    def test_diacritics_are_normalized(self):
        """Samma uttryck med och utan diakriter ska matcha."""
        a = self._resolve('förra månaden')
        b = self._resolve('forra manaden')
        self.assertEqual(a['date_from'], b['date_from'])
