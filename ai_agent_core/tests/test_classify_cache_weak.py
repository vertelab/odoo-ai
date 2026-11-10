# -*- coding: utf-8 -*-
"""Tester för cache-nycklingen i access-resolvern (D4, 5.1–5.5).

VARFÖR DESSA TESTER FINNS: `_CLASSIFY_CACHE` nycklades på `env.cr` i en
vanlig dict. En dict håller sina nycklar vid liv, så varje cursor blev en
permanent post i workern — den kunde aldrig GC:as. En `project.task`-post
är dessutom en lista med ~50 000 id:n (PARTIAL), så posten skalade med
antalet rader i modellen.

`WeakKeyDictionary` nycklas fortfarande på cursorn men äger den inte.
Bara `kind` cachas; id-listan byggs vid behov.
"""

import gc
import sys
import weakref

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.models.ai_access_resolver import (
    _CLASSIFY_CACHE,
)


@tagged('okf', 'access', 'post_install', '-at_install')
class TestClassifyCacheWeak(TransactionCase):
    """5.1 + 5.2: cursorn kan weak-refereras och cachen håller den inte."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Resolver = cls.env['ai.access.resolver']
        cls.user = cls.env.ref('base.user_admin')

    def test_cursor_is_weak_referenceable(self):
        """5.1: `weakref.ref(cursor)` får inte kasta TypeError.

        Detta avgör om D4 eller fallback D4b används. Verifierat 2026-11-02:
        `odoo.sql_db.Cursor` har inget `__slots__` och ingen
        `__eq__`/`__hash__`-override, så både weak-ref och identitetshash
        fungerar.
        """
        ref = weakref.ref(self.env.cr)
        self.assertIs(ref(), self.env.cr)

    def test_cache_is_a_weak_key_dictionary(self):
        """5.2: cachen ska vara en WeakKeyDictionary, inte en dict."""
        self.assertIsInstance(_CLASSIFY_CACHE, weakref.WeakKeyDictionary)

    def test_entry_disappears_when_cursor_is_gc(self):
        """5.2: en post försvinner när cursorn GC:as.

        Vi skapar en riktig cursor (inte env.cr — den ägs av testramverket
        och lever hela testet), lägger en post i cachen och släpper den.
        """
        from odoo.sql_db import db_connect

        dbname = self.env.cr.dbname
        cr = db_connect(dbname).cursor()
        try:
            _CLASSIFY_CACHE[cr] = {'dummy': 'post'}
            self.assertEqual(len(_CLASSIFY_CACHE), 1)
            ref = weakref.ref(cr)
        finally:
            cr.close()

        del cr
        gc.collect()

        self.assertIsNone(ref(), 'cursorn ska kunna GC:as — cachen äger den inte')
        self.assertEqual(
            len(_CLASSIFY_CACHE), 0,
            'posten ska försvinna när cursorn GC:as')


@tagged('okf', 'access', 'post_install', '-at_install')
class TestPartialIdsNotCached(TransactionCase):
    """5.4 + 5.5: id-listan för PARTIAL cachas inte."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Resolver = cls.env['ai.access.resolver']
        cls.user = cls.env.ref('base.user_admin')

    def test_cache_holds_kind_not_id_list(self):
        """5.4: cachen ska bära `kind` (en sträng), aldrig en id-lista."""
        self.Resolver._classify_source_model('res.company', self.user)
        cache = self.Resolver._classify_cache()
        for key, value in cache.items():
            self.assertIsInstance(
                value, str,
                'cachen får bara bära kind (str), inte %r för %r'
                % (type(value), key))

    def test_partial_ids_are_rebuilt_on_demand(self):
        """5.4: `_partial_ids` ger samma lista som den ocachade vägen."""
        kind, ids = self.Resolver._classify_source_model_uncached(
            'res.partner', self.user)
        if kind != self.Resolver.PARTIAL:
            self.skipTest('res.partner klassas inte PARTIAL i denna DB')
        rebuilt = self.Resolver._partial_ids('res.partner', self.user)
        self.assertEqual(sorted(rebuilt), sorted(ids or []))

    def test_partial_ids_do_not_scale_the_cache_entry(self):
        """5.5: cachepostens storlek ska inte bero på antalet rader.

        Före 5.4 var posten `(kind, ids)` — för `project.task` ~50 000
        ints. Nu är den `kind`, en kort sträng. Vi mäter `sys.getsizeof`
        på det som faktiskt ligger i cachen.
        """
        self.Resolver._classify_source_model('res.partner', self.user)
        cache = self.Resolver._classify_cache()
        entry = cache[('res.partner', self.user.id)]
        size = sys.getsizeof(entry)
        # En sträng på några tecken: långt under en lista med tusentals ints
        # (en lista med 10 000 ints är ~80 KB bara i pekare).
        self.assertLess(
            size, 1000,
            'cacheposten ska vara liten och oberoende av radantalet '
            '(mätt: %d B)' % size)
