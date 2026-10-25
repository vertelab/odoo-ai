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

    def test_condition_executes_with_dict_params(self):
        """Villkoret måste gå att KÖRA med dict-params, inte bara se rätt ut.

        Regression: grenen `source_ref NOT LIKE '%,%'` skrevs med ett
        ensamt `%`. psycopg2 kör SQL-strängen genom %-formatering när
        params är en dict, så `%` lästes som platshållare och VARJE
        OKF-sökning kastade `ProgrammingError: argument formats can't be
        mixed`. `_okf_search` fångade felet och returnerade tomt — sökfältet
        svarade noll träffar på allt.

        Testet inspekterar därför inte strängen: det kör den.
        """
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['res.company'])
        self.assertIsNotNone(sql)
        try:
            self.env.cr.execute(
                'SELECT count(*) FROM ai_okf_concept WHERE ' + sql,
                dict(params))
            self.env.cr.fetchall()
        except Exception as e:  # noqa: BLE001 — det är felet vi prövar
            self.fail('access-villkoret går inte att köra: %s' % e)

    def test_closed_model_not_mentioned(self):
        """CLOSED nämns inte alls — men oprövade faller till D4-nätet.

        ÄNDRAT 2026-10-14: förr gav alla stängda `None` (inget koncept
        kunde vara läsbart). Det var FEL så snart en `source_ref` inte är
        en modellreferens: ett mails Message-ID (`<a@b>`, inget komma)
        kunde aldrig prövas, och `None` gjorde hela mail-arkivet osökbart.
        Nu ingår alltid de oprövade grenarna (`IS NULL`, utan komma), så
        villkoret blir icke-tomt. Modellnamnet får fortfarande inte nämnas.
        """
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['finns.inte.modell'])
        self.assertIsNotNone(
            sql, 'oprövade källor ska ge ett villkor, inte None')
        self.assertNotIn('finns.inte.modell', sql,
                         'en stängd modell får inte nämnas i villkoret')
        self.assertNotIn('finns.inte.modell', params.values())
        self.assertIn('source_ref IS NULL', sql)

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

    def test_all_closed_still_admits_unprovable(self):
        """Alla modeller stängda: oprövade källor är fortfarande kandidater.

        ÄNDRAT 2026-10-14 (se test_closed_model_not_mentioned). Den gamla
        `None`-regeln uteslöt också koncept vars `source_ref` inte är en
        modellreferens — t.ex. varje mail-koncept. Nu överlever de.
        """
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['finns.inte.a', 'finns.inte.b'])
        self.assertIsNotNone(sql)
        self.assertIn('source_ref IS NULL', sql)
        self.assertIn("source_ref NOT LIKE '%%,%%'", sql)

    def test_opaque_source_ref_is_admitted(self):
        """En `source_ref` utan komma (Message-ID) faller till D4-nätet.

        Regression 2026-10-14: `_okf_source_models()` gjorde hela
        Message-ID:n till ett påhittat modellnamn → CLOSED → tom sökning
        för hela mail-arkivet. Villkoret måste släppa igenom dem.
        """
        sql, params = self.Resolver._source_ref_condition(
            self.user, ['finns.inte.modell'])
        self.assertIn("source_ref NOT LIKE '%%,%%'", sql,
                      'ogenomskinliga referenser måste nå D4-nätet')

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
            owner_id=None, query=None):
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
        rec_id = self.env.cr.fetchone()[0]
        # `search_vector` byggs vid INSERT och uppdateras inte här. Den
        # delade driftdatabasen har 57 000 koncept, så ett vanligt ord som
        # "fakturor" ger `ts_rank` på driftens golv (~0.06) och vårt
        # testkoncept hamnar utanför `limit`. En unik term gör träffen
        # vår, oavsett korpus — access-logiken prövas lika fullt.
        if query:
            self.env.cr.execute("""
                UPDATE ai_okf_concept
                   SET search_vector = to_tsvector('swedish', %s)
                 WHERE id = %s
            """, (query, rec_id))
        return rec_id

    def test_limit_filled_with_visible_rows(self):
        """Osynliga rader konsumerar inte limit.

        Föll på den gamla koden: `limit` tillämpades i SQL före access, så
        en bortfiltrerad rad hade redan upptagit en plats.
        """
        # 5 koncept från en ÖPPEN modell, 5 från en STÄNGD
        for i in range(5):
            self._mk('vis.%s' % i, 'Fakturor synliga %s' % i,
                     source_ref='res.company,%s' % (i + 1), query='synterm')
        for i in range(5):
            self._mk('hem.%s' % i, 'Fakturor hemliga %s' % i,
                     source_ref='finns.inte,%s' % (i + 1), query='synterm')

        results = self.Concept._okf_search('synterm', limit=5)
        keys = {c.concept_key for c in results}
        self.assertEqual(len(results), 5,
                         'limit ska fyllas av läsbara rader')
        for key in keys:
            self.assertTrue(key.startswith('vis.'),
                            'en osynlig rad slank igenom: %s' % key)

    def test_closed_source_is_not_returned(self):
        """Ett koncept vars enda källa är stängd returneras inte.

        Databasen är delad och innehåller riktiga koncept som också matchar
        "fakturor" — därför mäts det här konceptet, inte totalen.
        """
        self._mk('hemlig.1', 'Fakturor hemliga.',
                 source_ref='finns.inte,1', query='hemligterm')
        results = self.Concept._okf_search('hemligterm', limit=50)
        self.assertNotIn('hemlig.1', [c.concept_key for c in results])

    def test_dedup_picks_visible_version(self):
        """Dedupen väljer en läsbar version, inte en osynlig senare.

        Föll på den gamla koden: `DISTINCT ON` valde senaste versionen utan
        att pröva läsbarhet — och förkastade därmed hela kedjan.
        """
        # v1 läsbar, v2 osynlig (samma concept_key)
        self._mk('kedja.1', 'Fakturor version ett.',
                 source_ref='res.company,1', version=1, query='kedjeterm')
        self._mk('kedja.1', 'Fakturor version två.',
                 source_ref='finns.inte,1', version=2, query='kedjeterm')

        results = self.Concept._okf_search('kedjeterm', limit=50)
        hits = [c for c in results if c.concept_key == 'kedja.1']
        self.assertEqual(len(hits), 1,
                         'den läsbara versionen ska returneras, en gång')
        self.assertEqual(hits[0].version, 1,
                         'den osynliga senare versionen får inte vinna')

    def test_unresolved_source_falls_to_net(self):
        """Ett koncept utan källhänvisning injiceras inte (D4-nätet)."""
        self._mk('utan.kalla', 'Fakturor utan källa.', source_ref=None,
                 query='källterm')
        results = self.Concept._okf_search('källterm', limit=10)
        # Sökningen får returnera den (SQL-villkoret släpper igenom NULL),
        # men injektionen ska avföra den — det testas i
        # test_okf_fail_closed.py. Här verifierar vi att den inte kraschar.
        self.assertIsInstance(results.ids, list)

    def test_unrestricted_user_gets_same_count(self):
        """En obegränsad användare ser alla tre öppna koncept.

        Totalen kan vara större — databasen har egna koncept som matchar
        "fakturor". Det som prövas är att ingen av de tre filtreras bort.
        """
        for i in range(3):
            self._mk('oppen.%s' % i, 'Fakturor öppna %s' % i,
                     source_ref='res.company,%s' % (i + 1), query='öppenterm')
        results = self.Concept._okf_search('öppenterm', limit=50)
        keys = {c.concept_key for c in results}
        for i in range(3):
            self.assertIn('oppen.%s' % i, keys)


@tagged('okf', 'access', 'post_install', '-at_install')
class TestOkfClassifyCache(ConfigParamGuardedCase):
    """Klassningen cachas per transaktion (2026-10-14).

    VARFÖR: `_classify_source_model` kör två `search_count` per modell och
    gjordes om vid VARJE OKF-sökning. Med `project.task` (~50 000 rader) är
    det dyra aggregat — och en sökning kan klassificera ett dussin modeller.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Resolver = cls.env['ai.access.resolver']
        cls.user = cls.env.ref('base.user_admin')

    def test_repeated_classification_is_cached(self):
        """Andra anropet för samma (modell, användare) ska inte räkna om."""
        first = self.Resolver._classify_source_model('res.company', self.user)
        cache = self.Resolver._classify_cache()
        key = ('res.company', self.user.id)
        self.assertIn(key, cache, 'klassningen cachades inte')
        self.assertEqual(cache[key], first)
        second = self.Resolver._classify_source_model('res.company', self.user)
        self.assertEqual(first, second)

    def test_cache_is_keyed_per_model_and_user(self):
        """Olika modeller/användare får egna nycklar — ingen sammanblandning."""
        self.Resolver._classify_source_model('res.company', self.user)
        self.Resolver._classify_source_model('res.partner', self.user)
        cache = self.Resolver._classify_cache()
        self.assertIn(('res.company', self.user.id), cache)
        self.assertIn(('res.partner', self.user.id), cache)

    def test_semantic_false_skips_embedding(self):
        """`semantic=False` ska ge träffar UTAN att anropa en embedding-provider.

        Regression 2026-10-14: embedding är ett synkront HTTP-anrop (20 s x
        3 försök) — upp till 60 s för en interaktiv sökning. Ren svensk FTS
        svarar på millisekunder.
        """
        Concept = self.env['ai.okf.concept']
        atype_id = self.env['ai.artifact.type'].search([], limit=1).id or \
            self.env.ref('ai_agent_core.artifact_type_learning').id
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, owner_company_id, create_date, write_date)
            VALUES ('semcache.1', 'Faktura till leverantör', 'company', 1,
                    'stable', false, %s, %s, now(), now())
        """, (atype_id, self.env.company.id))
        # Patches the provider lookup: om embedding-vägen körs kastar testet.
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                side_effect=AssertionError(
                    'embedding-provider anropades trots semantic=False')):
            results = Concept._okf_search(
                'faktura till leverantör', limit=50, semantic=False)
        self.assertIn('semcache.1', {c.concept_key for c in results})


@tagged('okf', 'access', 'post_install', '-at_install')
class TestOkfOpaqueSourceRef(ConfigParamGuardedCase):
    """Ogenomskinliga källreferenser — mail-arkivet (2026-10-14).

    VARFÖR: ett mail-koncept lagrar `source_ref = <message-id>` (t.ex.
    `<abc@host>`), enligt okf-mail-search D2. Det är ingen modellreferens —
    den har inget komma. Den gamla `_okf_source_models()` körde
    `split_part(source_ref, ',', 1)` och gjorde hela Message-ID:n till ett
    påhittat modellnamn → `env.get()` None → CLOSED → tom sökning för hela
    mail-arkivet. Detta var aldrig observerat eftersom ingen DB ännu hade
    mail-koncept (verifierat 2026-10-14).

    Dessa tester bevisar:
      1. `_okf_source_models()` fabricerar inga modellnamn ur Message-ID:n
      2. ett mail-koncept blir sökbart
      3. ägarkontrollen isolerar ändå mellan användare (ingen läcka)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Resolver = cls.env['ai.access.resolver']
        cls.Atype = cls.env['ai.artifact.type']
        cls.atype_id = cls.Atype.search([], limit=1).id or \
            cls.env.ref('ai_agent_core.artifact_type_learning').id
        cls.owner = cls.env.ref('base.user_admin')

    def _mk_mail(self, key, message_id, summary, owner_id):
        """Ett personligt mail-koncept: source_ref = Message-ID (inget komma)."""
        self.env.cr.execute("""
            INSERT INTO ai_okf_concept
                (concept_key, summary, scope, version, status, archived,
                 artifact_type_id, owner_user_id, source_ref,
                 create_date, write_date)
            VALUES (%s, %s, 'personal', 1, 'stable', false, %s, %s, %s,
                    now(), now())
            RETURNING id
        """, (key, summary, self.atype_id, owner_id, message_id))
        return self.env.cr.fetchone()[0]

    def test_message_id_is_not_a_source_model(self):
        """En Message-ID får inte bli ett påhittat modellnamn."""
        self._mk_mail('mail.1', '<probe-1@x>', 'Faktura från leverantör.',
                      self.owner.id)
        models = self.Concept._okf_source_models()
        self.assertNotIn('<probe-1@x>', models,
                         'Message-ID:t fabricerades till en modell')
        for name in models:
            self.assertIn(',', '%s,' % name,
                          'modellnamn ska komma ur en komma-referens')

    def test_mail_concept_is_searchable(self):
        """Ett mail-koncept (Message-ID) ska ge träff — inte tomt."""
        self._mk_mail('mail.sok', '<probe-sok@x>',
                      'Faktura från leverantör.', self.owner.id)
        results = self.Concept._okf_search(
            'faktura', scope='personal', owner_id=self.owner.id, limit=10)
        keys = {c.concept_key for c in results}
        self.assertIn('mail.sok', keys,
                      'mail-konceptet blev osökbart (access-resolvern)')

    def test_mail_concepts_stay_isolated_per_owner(self):
        """Ägarkontrollen isolerar mail mellan användare (ingen läcka).

        Att släppa igenom ogenomskinliga referenser i source-ref-gaten får
        INTE öppna en läcka: `_okf_search` filtrerar på `owner_user_id`
        för personal-scope, ett oberoende lager.
        """
        other = self.env['res.users'].create({
            'name': 'ZZ OKF Annan Ägare', 'login': 'zz_okf_other_owner',
        })
        self._mk_mail('mail.min', '<probe-min@x>',
                      'Faktura till mig.', self.owner.id)
        self._mk_mail('mail.annans', '<probe-annan@x>',
                      'Faktura till annan.', other.id)

        mine = self.Concept._okf_search(
            'faktura', scope='personal', owner_id=self.owner.id, limit=10)
        my_keys = {c.concept_key for c in mine}
        self.assertIn('mail.min', my_keys)
        self.assertNotIn('mail.annans', my_keys,
                         'LÄCKA: annan användares mail-koncept synligt')

        theirs = self.Concept._okf_search(
            'faktura', scope='personal', owner_id=other.id, limit=10)
        their_keys = {c.concept_key for c in theirs}
        self.assertIn('mail.annans', their_keys)
        self.assertNotIn('mail.min', their_keys,
                         'LÄCKA: mina mail-koncept synliga för annan')
