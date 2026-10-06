# -*- coding: utf-8 -*-
"""Ägarmedvetenhet i OKF (okf-owner-and-access-scoping D5–D7).

VARFÖR: `_okf_index_record_one()` anropade `_okf_owner_vals()` EN gång och
skrev ETT koncept, och `_okf_concept_key()` var `'<modell>,<id>'` utan
ägare. Tre ställen grupperade på (scope, concept_key) UTAN ägare:

    _okf_upsert:        search([scope, concept_key, status])
    _okf_search SQL:    DISTINCT ON (scope, concept_key)
    _latest_per_key():  _read_group([scope, concept_key])

Det gjorde att A:s indexering kunde hitta B:s rad under samma nyckel och
skriva version 2 av B:s koncept — och markera B:s rad `superseded`. Det
kastar inget fel; det skriver in i fel användares minne. Det är den
allvarligaste buggen i ändringen.

Dessa tester bevisar:
  1. default-listan ger exakt ett koncept (beteende-bevarande)
  2. en post med N ägare ger N koncept, med ägaren i nyckeln
  3. flaggan rensas EN gång för alla ägare
  4. korsanvändarskrivningen är borta
  5. dedup och _latest_per_key skiljer två ägares kedjor
  6. nyckelgrammatiken skiljer ägar- från språkled
"""

import logging
from unittest.mock import patch

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

_logger = logging.getLogger(__name__)


class _MultiOwnerMemory(common.TransactionCase):
    """Bas: en modell som bär mixinen och kan ge flera ägare."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.Personal = cls.env['ai.personal.memory']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'learning')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'learning', 'kind': 'memory'})
        cls.user_a = cls.env['res.users'].create({
            'name': 'ZZ OKF Owner A', 'login': 'zz_okf_owner_a',
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'ZZ OKF Owner B', 'login': 'zz_okf_owner_b',
        })
        cls.user_c = cls.env['res.users'].create({
            'name': 'ZZ OKF Owner C', 'login': 'zz_okf_owner_c',
        })


@tagged('okf', 'post_install', '-at_install')
class TestOkfOwnerList(_MultiOwnerMemory):
    """D5: `_okf_owner_vals_list()` och N-ägar-loopen."""

    def test_default_owner_list_has_one_element(self):
        """En modell utan egen implementation ger exakt en ägare."""
        mem = self.Personal.new({
            'user_id': self.user_a.id,
            'content': 'Testminne',
        })
        owners = mem._okf_owner_vals_list()
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0], mem._okf_owner_vals())

    def test_default_owner_is_company(self):
        """Legacy-personligt minne ägs av sin användare (fas 6.3)."""
        mem = self.Personal.new({
            'user_id': self.user_a.id,
            'content': 'Testminne',
        })
        owners = mem._okf_owner_vals_list()
        self.assertEqual(owners[0].get('owner_user_id'), self.user_a.id)

    def test_three_owners_give_three_concepts(self):
        """En post med tre ägare ger tre koncept, ett per ägare."""
        mem = self.Personal.create({
            'user_id': self.user_a.id,
            'content': 'Ett minne med flera ägare.',
        })
        owners = [
            {'owner_user_id': self.user_a.id},
            {'owner_user_id': self.user_b.id},
            {'owner_user_id': self.user_c.id},
        ]
        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=owners):
            first = mem._okf_index_record()
        self.assertTrue(first)
        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id),
        ])
        self.assertEqual(len(concepts), 3)
        self.assertEqual(
            sorted(concepts.mapped('owner_user_id').ids),
            sorted([self.user_a.id, self.user_b.id, self.user_c.id]))

    def test_owners_get_distinct_concept_keys(self):
        """Två ägares koncept för samma post delar aldrig nyckel."""
        mem = self.Personal.create({
            'user_id': self.user_a.id,
            'content': 'Delat minne.',
        })
        owners = [
            {'owner_user_id': self.user_a.id},
            {'owner_user_id': self.user_b.id},
        ]
        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=owners):
            mem._okf_index_record()
        keys = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id),
        ]).mapped('concept_key')
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)

    def test_dirty_cleared_once_for_all_owners(self):
        """Flaggan rensas EN gång, inte en per ägare."""
        mem = self.Personal.create({
            'user_id': self.user_a.id,
            'content': 'Rensa en gång.',
        })
        self.assertTrue(mem.okf_dirty)
        owners = [
            {'owner_user_id': self.user_a.id},
            {'owner_user_id': self.user_b.id},
            {'owner_user_id': self.user_c.id},
        ]
        calls = []
        real = type(mem)._clear_okf_dirty

        def spy(self_, extra_vals=None):
            calls.append(extra_vals)
            return real(self_, extra_vals)

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=owners), \
                patch.object(type(mem), '_clear_okf_dirty', spy):
            mem._okf_index_record()
        self.assertEqual(len(calls), 1,
                         'flaggan ska rensas en gång, inte %s' % len(calls))
        self.assertFalse(mem.okf_dirty)

    def test_no_owner_created_keeps_dirty(self):
        """En post utan text skapar inget koncept MEN rensar flaggan.

        Beteendet är före ändringen: en post som avförs ("tomt för alltid")
        ska inte blockera kön för evigt.
        """
        mem = self.Personal.create({
            'user_id': self.user_a.id,
            'content': 'x',
        })
        with patch.object(type(mem), '_okf_body_source', return_value=''):
            result = mem._okf_index_record()
        self.assertFalse(result)
        self.assertFalse(mem.okf_dirty,
                         'avförd post ska rensa flaggan (tombstone)')

    def test_single_owner_keeps_key_format(self):
        """En enägd post behåller nyckelformatet UTAN ägarled.

        Ägarläget behövs bara när samma post ger N koncept. En
        `ai.personal.memory`-post tillhör exakt en användare — att suffixa
        ägaren där vore redundant och bytte nyckelformat för varje
        befintligt personligt koncept.
        """
        mem = self.Personal.create({
            'user_id': self.user_a.id,
            'content': 'En ägare.',
        })
        mem._okf_index_record()
        concept = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)])
        self.assertEqual(len(concept), 1)
        self.assertEqual(concept.concept_key,
                         'ai.personal.memory,%s' % mem.id,
                         'enägd post ska INTE få ägarled i nyckeln')


@tagged('okf', 'post_install', '-at_install')
class TestOkfCrossUserWrite(_MultiOwnerMemory):
    """D7: den tysta korsanvändarskrivningen."""

    def _mk_concept(self, key, user, version=1, summary='Text'):
        return self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'personal',
            'concept_key': key,
            'summary': summary,
            'owner_user_id': user.id,
        })

    def test_upsert_filters_by_owner(self):
        """A:s omskrivning rör inte B:s rad under samma nyckel.

        Föll på den gamla koden: `_okf_upsert` sökte utan ägarfilter och
        skrev version 2 av B:s koncept + `superseded` på B:s rad.
        """
        key = 'ai.personal.memory,999'
        a = self._mk_concept(key, self.user_a, summary='A:s text')
        b = self._mk_concept(key, self.user_b, summary='B:s text')
        # Skriv en ny version för A
        self.Concept._okf_upsert(
            self.atype, key, 'A:s nya text',
            owner_user_id=self.user_a.id, generated_by='test')
        a.invalidate_recordset()
        b.invalidate_recordset()
        self.assertEqual(b.version, 1, 'B:s version ska vara orörd')
        self.assertNotEqual(b.status, 'superseded',
                            'B:s rad får inte bli superseded av A:s skrivning')
        self.assertEqual(a.status, 'superseded',
                         'A:s föregående version ska bli superseded')

    def test_upsert_creates_new_row_for_other_owner(self):
        """En annan ägare får sin EGEN version 1, inte version 2."""
        key = 'ai.personal.memory,998'
        self._mk_concept(key, self.user_a)
        created = self.Concept._okf_upsert(
            self.atype, key, 'B:s text',
            owner_user_id=self.user_b.id, generated_by='test')
        self.assertEqual(created.version, 1)
        self.assertEqual(created.owner_user_id, self.user_b)

    def test_latest_per_key_separates_owners(self):
        """`_latest_per_key()` ger en rad per (scope, ägare, nyckel)."""
        key = 'ai.personal.memory,997'
        a = self._mk_concept(key, self.user_a)
        b = self._mk_concept(key, self.user_b)
        both = (a | b)
        latest = both._latest_per_key()
        self.assertEqual(len(latest), 2,
                         'två ägares kedjor får inte kollapsa till en rad')
        self.assertEqual(set(latest.ids), {a.id, b.id})

    def test_latest_per_key_keeps_latest_version(self):
        """Inom EN ägare väljs fortfarande senaste versionen."""
        key = 'ai.personal.memory,996'
        v1 = self._mk_concept(key, self.user_a, summary='gammal')
        v2 = self.Concept._okf_upsert(
            self.atype, key, 'ny text',
            owner_user_id=self.user_a.id, generated_by='test')
        latest = (v1 | v2)._latest_per_key()
        self.assertEqual(latest.ids, [v2.id])


@tagged('okf', 'post_install', '-at_install')
class TestOkfConceptKeyGrammar(_MultiOwnerMemory):
    """D6: nyckelgrammatiken — ägarled vs språkled."""

    def test_base_key_unchanged_without_owner(self):
        """Utan ägare är nyckeln oförändrad `'<modell>,<id>'`."""
        mem = self.Personal.new({'user_id': self.user_a.id, 'content': 'x'})
        self.assertEqual(mem._okf_concept_key(),
                         'ai.personal.memory,%s' % mem.id)

    def test_owner_suffix(self):
        """Med ägare bär nyckeln `user.<uid>`."""
        mem = self.Personal.new({'user_id': self.user_a.id, 'content': 'x'})
        key = mem._okf_concept_key(owner_id='user.%s' % self.user_a.id)
        self.assertTrue(key.endswith(',user.%s' % self.user_a.id), key)

    def test_language_and_owner_suffix(self):
        """Med båda är ägarledet före språkledet."""
        mem = self.Personal.new({'user_id': self.user_a.id, 'content': 'x'})
        key = mem._okf_concept_key(
            lang='sv_SE', owner_id='user.%s' % self.user_a.id)
        self.assertTrue(key.endswith(',user.%s,sv_SE' % self.user_a.id), key)

    def test_lookup_keys_base_form(self):
        """Basnyckeln ger sig själv."""
        self.assertEqual(
            self.Concept._okf_lookup_keys('ai.personal.memory,5'),
            ['ai.personal.memory,5'])

    def test_lookup_keys_language_suffix(self):
        """Språksuffix ger nyckeln + basnyckeln (1.9-beteendet)."""
        self.assertEqual(
            self.Concept._okf_lookup_keys('website.page,5,sv_SE'),
            ['website.page,5,sv_SE', 'website.page,5'])

    def test_lookup_keys_owner_suffix(self):
        """Ägarsuffix ger nyckeln + basnyckeln."""
        self.assertEqual(
            self.Concept._okf_lookup_keys('ai.personal.memory,5,user.7'),
            ['ai.personal.memory,5,user.7', 'ai.personal.memory,5'])

    def test_lookup_keys_owner_and_language(self):
        """Båda suffixen ger nyckeln + basnyckeln."""
        self.assertEqual(
            self.Concept._okf_lookup_keys(
                'ai.personal.memory,5,user.7,sv_SE'),
            ['ai.personal.memory,5,user.7,sv_SE', 'ai.personal.memory,5'])

    def test_existing_chain_continues(self):
        """En befintlig rad utan suffix fortsätter sin kedja."""
        key = 'ai.personal.memory,995'
        v1 = self.Concept.create({
            'artifact_type_id': self.atype.id,
            'scope': 'personal',
            'concept_key': key,
            'summary': 'Utan suffix',
            'owner_user_id': self.user_a.id,
        })
        # Ny indexering med ägarsuffix ska hitta den befintliga kedjan
        new = self.Concept._okf_upsert(
            self.atype, '%s,user.%s' % (key, self.user_a.id), 'Ny text',
            owner_user_id=self.user_a.id, generated_by='test')
        v1.invalidate_recordset()
        self.assertEqual(new.version, 2,
                         'befintlig kedja ska fortsätta, inte starta om')
        self.assertEqual(v1.status, 'superseded')
