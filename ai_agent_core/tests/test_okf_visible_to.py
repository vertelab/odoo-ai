# -*- coding: utf-8 -*-
"""visible_to() — användarvänd sökning (dms-global-search D1, D2).

VARFÖR: den enda användarvända sökingången i drift var en server action som
läste `env.context.get('search_query')` — en nyckel ingenting sätter — och
föll tillbaka på den bokstavliga strängen 'senaste', med `scope=None`.
`_okf_search` loggar det sistnämnda som "bred sökning" och returnerar koncept
över ALLA ägare.

`visible_to()` är svaret, och det svarar på frågan "ska allt vara globalt
sökbart?" med: globalt NÅBART, aldrig globalt SCOPAT.

Dessa tester bevisar:
  1. company-koncept syns för alla i samma company
  2. en annan användares personal-koncept syns ALDRIG (testet faller om
     scope-filtret tas bort)
  3. användarens egna personal-koncept syns
  4. coworker-koncept följer sin ägare (chat_user_id)
  5. samma koncept nådd via två par dedupas till en rad
  6. `scope=None` anropas aldrig
"""

import logging
from unittest.mock import patch

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

_logger = logging.getLogger(__name__)


@tagged('okf', 'search', 'post_install', '-at_install')
class TestOkfVisibleTo(ConfigParamGuardedCase):
    """D2: resolvern — den vidgar aldrig ett scope."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Okf = cls.env['ai.okf.concept']
        cls.company = cls.env.company
        cls.user_a = cls.env['res.users'].create({
            'name': 'ZZ Visible A', 'login': 'zz_visible_a',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'ZZ Visible B', 'login': 'zz_visible_b',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
        })

    def _concept(self, key, summary, **kw):
        """Skapa ett koncept med en unik sökterm i sammanfattningen."""
        return self.Okf._okf_upsert(
            'learning', concept_key=key, summary=summary,
            generated_by='test', **kw)

    # ── 1. Company ────────────────────────────────────────────────────

    def test_company_concept_is_visible_to_everyone(self):
        """Company-scope hör till det delade lagret."""
        self._concept('vis.company', 'zebrafisk company note',
                      owner_company_id=self.company.id)
        hits_a = self.Okf.visible_to('zebrafisk', user=self.user_a)
        hits_b = self.Okf.visible_to('zebrafisk', user=self.user_b)
        self.assertIn('vis.company', hits_a.mapped('concept_key'))
        self.assertIn('vis.company', hits_b.mapped('concept_key'))

    # ── 2. Personal — läckagetestet ───────────────────────────────────

    def test_other_users_personal_concept_is_invisible(self):
        """A:s personliga koncept får ALDRIG synas för B.

        Detta är testet som ska falla om scope-filtret tas bort ur
        `_visible_owner_pairs()` eller om `visible_to()` börjar skicka
        `scope=None`.
        """
        self._concept('vis.personal.a', 'guldfasan personal note',
                      owner_user_id=self.user_a.id)
        hits_b = self.Okf.visible_to('guldfasan', user=self.user_b)
        self.assertNotIn(
            'vis.personal.a', hits_b.mapped('concept_key'),
            'B såg A:s personliga koncept — scope-filtret är borta')

    def test_own_personal_concept_is_visible(self):
        """Användarens egna personliga koncept ska synas för hen."""
        self._concept('vis.personal.own', 'silverräv own note',
                      owner_user_id=self.user_a.id)
        hits = self.Okf.visible_to('silverräv', user=self.user_a)
        self.assertIn('vis.personal.own', hits.mapped('concept_key'))

    # ── 3. Coworker ───────────────────────────────────────────────────

    def test_coworker_concept_follows_its_owner(self):
        """En coworker vars chat_user_id är A syns för A, inte för B."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ Visible Coworker',
            'chat_user_id': self.user_a.id,
        })
        self._concept('vis.coworker.a', 'kopparorm coworker note',
                      owner_coworker_id=coworker.id)
        hits_a = self.Okf.visible_to('kopparorm', user=self.user_a)
        hits_b = self.Okf.visible_to('kopparorm', user=self.user_b)
        self.assertIn('vis.coworker.a', hits_a.mapped('concept_key'))
        self.assertNotIn('vis.coworker.a', hits_b.mapped('concept_key'))

    # ── 4. Paren ──────────────────────────────────────────────────────

    def test_owner_pairs_include_company_personal_and_owned_coworkers(self):
        """Paren löses ur company, user och ai.coworker — aldrig gissat."""
        c1 = self.env['ai.coworker'].create({
            'name': 'ZZ Pair One', 'chat_user_id': self.user_a.id})
        c2 = self.env['ai.coworker'].create({
            'name': 'ZZ Pair Two', 'chat_user_id': self.user_a.id})
        pairs = self.Okf._visible_owner_pairs(user=self.user_a)
        self.assertIn(('company', self.company.id), pairs)
        self.assertIn(('personal', self.user_a.id), pairs)
        self.assertIn(('coworker', c1.id), pairs)
        self.assertIn(('coworker', c2.id), pairs)

    def test_owner_pairs_never_contain_none_owner(self):
        """Ett par utan ägare är exakt vad _okf_search kastar på."""
        for scope, owner in self.Okf._visible_owner_pairs(user=self.user_a):
            self.assertIsNotNone(owner, '%s hade ingen ägare' % scope)
            self.assertIn(scope, ('company', 'personal', 'coworker'))

    # ── 5. Dedup och sortering ────────────────────────────────────────

    def test_same_concept_reached_through_two_pairs_appears_once(self):
        """Ett koncept nått via två par ska bara dyka upp en gång.

        `_latest_per_key()` dedupar på (scope, ägare, concept_key). Samma
        `concept_key` under två OLIKA ägare är två koncept — men samma
        koncept får inte räknas dubbelt när paren överlappar.
        """
        self._concept('vis.dup', 'tigerhaj duplicate note',
                      owner_company_id=self.company.id)
        hits = self.Okf.visible_to('tigerhaj', user=self.user_a)
        keys = [k for k in hits.mapped('concept_key') if k == 'vis.dup']
        self.assertEqual(len(keys), 1, 'samma koncept returnerades två gånger')

    def test_limit_is_respected(self):
        """Taket gäller totalen, inte per par."""
        for i in range(5):
            self._concept('vis.limit.%d' % i, 'varggrå limit note %d' % i,
                          owner_company_id=self.company.id)
        hits = self.Okf.visible_to('varggrå', user=self.user_a, limit=2)
        self.assertLessEqual(len(hits), 2)

    # ── 6. Aldrig scope=None ──────────────────────────────────────────

    def test_never_calls_okf_search_without_scope(self):
        """`scope=None` är den breda sökningen — den får aldrig användas.

        Det var precis det den gamla `Hybridsök (AI)`-actionen gjorde.
        """
        self._concept('vis.scope', 'örnscope guard note',
                      owner_company_id=self.company.id)
        seen = []
        original = type(self.Okf)._okf_search

        def _spy(self_, query, scope=None, **kw):
            seen.append(scope)
            return original(self_, query, scope=scope, **kw)

        with patch.object(type(self.Okf), '_okf_search', _spy):
            self.Okf.visible_to('örnscope', user=self.user_a)
        self.assertTrue(seen, 'visible_to anropade aldrig _okf_search')
        self.assertNotIn(None, seen, 'visible_to skickade scope=None')

    # ── 7. Tomt är tomt ───────────────────────────────────────────────

    def test_empty_query_returns_nothing(self):
        """Ingen fråga = inget svar, inte 'senaste'."""
        self.assertFalse(self.Okf.visible_to('', user=self.user_a))
        self.assertFalse(self.Okf.visible_to('   ', user=self.user_a))

    def test_nonsense_query_returns_nothing(self):
        """En fråga utan träff är tom — ingen create_date-fallback."""
        hits = self.Okf.visible_to('xyzzyplughqqq', user=self.user_a)
        self.assertFalse(hits, 'nonsensfråga gav träffar (tyst fallback)')


@tagged('okf', 'search', 'post_install', '-at_install')
class TestOkfVisibleToThreeUsers(ConfigParamGuardedCase):
    """5.1: tre användartyper ser exakt sin tillåtna mängd.

    Admin, vanlig intern användare och coworker-ägare. Testet bevisar att
    mängderna skiljer sig ÅT — inte bara att var och en är icke-tom.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Okf = cls.env['ai.okf.concept']
        cls.company = cls.env.company
        cls.admin = cls.env.ref('base.user_admin')
        cls.plain = cls.env['res.users'].create({
            'name': 'ZZ Three Plain', 'login': 'zz_three_plain',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
        })
        cls.owner = cls.env['res.users'].create({
            'name': 'ZZ Three Owner', 'login': 'zz_three_owner',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
        })
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'ZZ Three Coworker',
            'chat_user_id': cls.owner.id,
        })

    def _concept(self, key, summary, **kw):
        return self.Okf._okf_upsert(
            'learning', concept_key=key, summary=summary,
            generated_by='test', **kw)

    def test_three_users_see_three_different_sets(self):
        """Samma fråga, tre användare, tre mängder."""
        self._concept('three.company', 'enhörning company',
                      owner_company_id=self.company.id)
        self._concept('three.plain', 'enhörning plain personal',
                      owner_user_id=self.plain.id)
        self._concept('three.owner', 'enhörning owner personal',
                      owner_user_id=self.owner.id)
        self._concept('three.coworker', 'enhörning coworker',
                      owner_coworker_id=self.coworker.id)

        def keys(user):
            return set(self.Okf.visible_to('enhörning', user=user)
                       .mapped('concept_key'))

        admin_keys = keys(self.admin)
        plain_keys = keys(self.plain)
        owner_keys = keys(self.owner)

        # Alla ser bolagskonceptet.
        for name, ks in (('admin', admin_keys), ('plain', plain_keys),
                         ('owner', owner_keys)):
            self.assertIn('three.company', ks, '%s såg inte bolaget' % name)

        # Den vanliga användaren ser sitt eget — och bara sitt eget.
        self.assertIn('three.plain', plain_keys)
        self.assertNotIn('three.owner', plain_keys)
        self.assertNotIn('three.coworker', plain_keys)

        # Coworker-ägaren ser sitt eget OCH coworkerns.
        self.assertIn('three.owner', owner_keys)
        self.assertIn('three.coworker', owner_keys)
        self.assertNotIn('three.plain', owner_keys)

        # Mängderna är faktiskt olika — inte bara icke-tomma.
        self.assertNotEqual(plain_keys, owner_keys,
                            'två användare med olika ägande såg samma mängd')
