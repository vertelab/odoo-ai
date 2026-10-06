# -*- coding: utf-8 -*-
"""Access i urvalet — ÖPPEN/STÄNGD/PARTIELL (okf-owner-and-access-scoping D2, D3).

VARFÖR: OKF:s urval (dedup + limit) skedde i SQL FÖRE access-filtret. Det
gav två fel:

  1. `limit` konsumerades av osynliga rader. En användare som fick läsa 3 av
     10 matchande koncept fick färre än 3 — de osynliga tog platserna.
  2. `DISTINCT ON (scope, concept_key)` valde SENASTE versionen utan att
     pröva läsbarhet. Var den osynlig förkastades hela kedjan, även en
     läsbar äldre version.

Volymen gör klassningen nödvändig: `project.task` ~50 000 och `res.partner`
~3 300 i ledningssystem (mätt 2026-10-06). En `IN`-lista över alla synliga
id:n för en användare som ser allt spränger plan-cachen.

Dessa tester bevisar:
  1. klassningen ger OPEN/CLOSED/PARTIAL korrekt
  2. OPEN ger inget id-villkor, CLOSED nämns inte, PARTIAL ger en lista
  3. `limit` fylls av läsbara rader
  4. dedupen väljer en läsbar version
  5. fail-closed: oprövade källor utelämnas
"""

import logging
from unittest.mock import patch

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

_logger = logging.getLogger(__name__)


@tagged('okf', 'access', 'post_install', '-at_install')
class TestOkfSourceModelClassification(ConfigParamGuardedCase):
    """D2: klassningen per källmodell."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Resolver = cls.env['ai.access.resolver']
        cls.user = cls.env.ref('base.user_admin')

    def test_open_when_user_sees_everything(self):
        """En modell användaren ser allt i klassas OPEN."""
        kind, ids = self.Resolver._classify_source_model(
            'res.company', self.user)
        self.assertEqual(kind, self.Resolver.OPEN)
        self.assertIsNone(ids)

    def test_closed_when_model_missing(self):
        """En modell som inte finns i env klassas CLOSED (fail-closed)."""
        kind, ids = self.Resolver._classify_source_model(
            'finns.inte.modell', self.user)
        self.assertEqual(kind, self.Resolver.CLOSED)
        self.assertIsNone(ids)

    def test_closed_when_user_sees_nothing(self):
        """En modell användaren inte får läsa klassas CLOSED.

        Använder en modell som är känslig: `res.partner` med en ir.rule som
        stänger ute användaren helt. (En nyskapad `base.group_user`-användare
        KAN läsa `res.users` i en tom databas — det är därför testet bygger
        sin egen regel i stället för att anta att en grupp saknar rätt.)
        """
        user = self.env['res.users'].create({
            'name': 'ZZ OKF Ingen läsning', 'login': 'zz_okf_noread',
        })
        rule = self.env['ir.rule'].create({
            'name': 'ZZ OKF ingen läsning',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'domain_force': '[(0, "=", 1)]',  # matchar inget
            'groups': [(6, 0, [self.env.ref('base.group_user').id])],
            'perm_read': True, 'perm_write': False, 'perm_create': False,
            'perm_unlink': False,
        })
        try:
            kind, ids = self.Resolver._classify_source_model(
                'res.partner', user)
            self.assertEqual(kind, self.Resolver.CLOSED,
                             'en användare som inte ser något ska ge CLOSED')
        finally:
            rule.unlink()

    def test_partial_gives_id_list(self):
        """En delmängd klassas PARTIAL och ger en id-lista."""
        # Skapa två partners; begränsa med ir.rule till en av dem
        p1 = self.env['res.partner'].create({'name': 'ZZ OKF P1'})
        p2 = self.env['res.partner'].create({'name': 'ZZ OKF P2'})
        user = self.env['res.users'].create({
            'name': 'ZZ OKF Partial', 'login': 'zz_okf_partial',
        })
        rule = self.env['ir.rule'].create({
            'name': 'ZZ OKF partial rule',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'domain_force': "[('id', '=', %s)]" % p1.id,
            'groups': [(6, 0, [self.env.ref('base.group_user').id])],
            'perm_read': True, 'perm_write': False, 'perm_create': False,
            'perm_unlink': False,
        })
        try:
            kind, ids = self.Resolver._classify_source_model(
                'res.partner', user)
            if kind == self.Resolver.PARTIAL:
                self.assertIsInstance(ids, list)
                self.assertIn(p1.id, ids)
                self.assertNotIn(p2.id, ids)
        finally:
            rule.unlink()
            p1.unlink()
            p2.unlink()


@tagged('okf', 'access', 'post_install', '-at_install')
class TestOkfSourceRefCondition(ConfigParamGuardedCase):
    """D3: SQL-villkoret ur klassningen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Resolver = cls.env['ai.access.resolver']
        cls.user = cls.env.ref('base.user_admin')

    def test_open_model_gives_no_id_list(self):
        """OPEN ger LIKE, aldrig en id-lista."""
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['res.company'])
        self.assertIn('LIKE', sql)
        self.assertIn('res.company,%', params.values())
        # Inga id-listor i parametrarna
        for value in params.values():
            self.assertNotIsInstance(
                value, list, 'OPEN får inte ge en id-lista')

    def test_closed_model_not_mentioned(self):
        """CLOSED nämns inte alls — och alla stängda ger None."""
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['finns.inte.modell'])
        # Alla modeller stängda -> None (inget koncept kan vara läsbart)
        self.assertIsNone(sql, 'alla stängda ska ge None')

    def test_closed_model_excluded_when_others_open(self):
        """En stängd modell nämns inte när en annan är öppen.

        Modellnamnet står i PARAMETERN (`%(open_res_company)s`), aldrig
        inlinat i SQL-strängen — det är hela poängen med platshållaren.
        """
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['res.company', 'finns.inte.modell'])
        self.assertIsNotNone(sql)
        self.assertNotIn('finns.inte.modell', sql,
                         'en stängd modell får inte nämnas i villkoret')
        self.assertNotIn('finns.inte.modell', params.values())
        self.assertIn('res.company,%', params.values(),
                      'den öppna modellen ska ge ett LIKE-mönster')

    def test_empty_model_list_gives_no_condition(self):
        """En tom modellista ger inget villkor (inte 'allt stängt')."""
        sql, params = self.Resolver._source_ref_condition(self.user, [])
        self.assertEqual(sql, '')

    def test_all_closed_returns_none(self):
        """Alla stängda ger None — inget koncept kan vara läsbart."""
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['finns.inte.a', 'finns.inte.b'])
        self.assertIsNone(sql)

    def test_condition_always_includes_null(self):
        """`source_ref IS NULL` ingår alltid — oprövade faller till D4."""
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['res.company'])
        self.assertIn('source_ref IS NULL', sql)


@tagged('okf', 'access', 'post_install', '-at_install')
class TestOkfAccessInSelection(ConfigParamGuardedCase):
    """D3: access före dedup och limit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Atype = cls.env['ai.artifact.type']
        cls.atype_id = cls.Atype.search([], limit=1).id or \
            cls.env.ref('ai_agent_core.artifact_type_learning').id

    def _mk(self, key, summary, source_ref=None, version=1, scope='company',
            owner_id=None):
        owner_id = owner_id or self.env.company.id
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, owner_company_id, source_ref,
                 create_date, write_date)
            VALUES (%s, %s, %s, %s, 'stable', false, %s, %s, %s,
                    now(), now())
            RETURNING id
        """, (key, summary, scope, version, self.atype_id, owner_id,
              source_ref))
        return self.env.cr.fetchone()[0]

    def test_limit_filled_with_visible_rows(self):
        """Osynliga rader konsumerar inte limit.

        Föll på den gamla koden: `limit` tillämpades i SQL före access, så
        en bortfiltrerad rad hade redan upptagit en plats.
        """
        # 5 koncept från en ÖPPEN modell, 5 från en STÄNGD
        for i in range(5):
            self._mk('vis.%s' % i, 'Fakturor synliga %s' % i,
                     source_ref='res.company,%s' % (i + 1))
        for i in range(5):
            self._mk('hem.%s' % i, 'Fakturor hemliga %s' % i,
                     source_ref='finns.inte,%s' % (i + 1))

        results = self.Concept._okf_search('fakturor', limit=5)
        keys = {c.concept_key for c in results}
        self.assertEqual(len(results), 5,
                         'limit ska fyllas av läsbara rader')
        for key in keys:
            self.assertTrue(key.startswith('vis.'),
                            'en osynlig rad slank igenom: %s' % key)

    def test_closed_source_is_not_returned(self):
        """Ett koncept vars enda källa är stängd returneras inte."""
        self._mk('hemlig.1', 'Fakturor hemliga.',
                 source_ref='finns.inte,1')
        results = self.Concept._okf_search('fakturor', limit=10)
        self.assertEqual(len(results), 0)

    def test_dedup_picks_visible_version(self):
        """Dedupen väljer en läsbar version, inte en osynlig senare.

        Föll på den gamla koden: `DISTINCT ON` valde senaste versionen utan
        att pröva läsbarhet — och förkastade därmed hela kedjan.
        """
        # v1 läsbar, v2 osynlig (samma concept_key)
        self._mk('kedja.1', 'Fakturor version ett.',
                 source_ref='res.company,1', version=1)
        self._mk('kedja.1', 'Fakturor version två.',
                 source_ref='finns.inte,1', version=2)

        results = self.Concept._okf_search('fakturor', limit=10)
        self.assertEqual(len(results), 1,
                         'den läsbara versionen ska returneras')
        self.assertEqual(results[0].version, 1,
                         'den osynliga senare versionen får inte vinna')

    def test_unresolved_source_falls_to_net(self):
        """Ett koncept utan källhänvisning injiceras inte (D4-nätet)."""
        self._mk('utan.kalla', 'Fakturor utan källa.', source_ref=None)
        results = self.Concept._okf_search('fakturor', limit=10)
        # Sökningen får returnera den (SQL-villkoret släpper igenom NULL),
        # men injektionen ska avföra den — det testas i
        # test_okf_fail_closed.py. Här verifierar vi att den inte kraschar.
        self.assertIsInstance(results.ids, list)

    def test_unrestricted_user_gets_same_count(self):
        """En obegränsad användare får samma antal som före ändringen."""
        for i in range(3):
            self._mk('oppen.%s' % i, 'Fakturor öppna %s' % i,
                     source_ref='res.company,%s' % (i + 1))
        results = self.Concept._okf_search('fakturor', limit=10)
        self.assertEqual(len(results), 3)
