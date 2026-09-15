# -*- coding: utf-8 -*-
"""Tester för konsolidering — lärande ur sessionens eftermäle (fas 5, D5/D6).

VARFÖR DESSA TESTER FINNS: `concept_key` avgör om en ny fakta ERSÄTTER en
tidigare version eller blir ett nytt koncept (`UNIQUE(scope, concept_key,
version)`). Den gamla nyckeln var `f'learned.{scope}.{session.id}.
{len(summary[:40])}'` — och `len(summary[:40])` är 40 för varje sammanfattning
längre än 40 tecken. Alla koncept i samma session fick alltså SAMMA nyckel och
kollapsade till en versionskedja: de skrev över varandra och fakta tappades
tyst.

Därför testar vi nyckelns BETYDelse: olika fakta → olika nycklar, samma fakta
→ samma nyckel (dedup), och att lärandet läser eftermälet i stället för
råsvansen.
"""

from unittest.mock import patch

from odoo.tests import common, tagged


@tagged('okf', 'learning', 'post_install', '-at_install')
class TestConceptKey(common.TransactionCase):
    """D6: concept_key får inte kollapsa olika fakta till samma nyckel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({'name': 'key-test'})

    def test_different_content_different_key(self):
        """Kärnan i buggen: gamla nyckeln gav samma värde för olika fakta."""
        a = self.coworker._normalize_concept_key(
            'Kunden föredrar fakturering via e-post.', 'personal')
        b = self.coworker._normalize_concept_key(
            'Projektet använder tvåveckorssprintar.', 'personal')
        self.assertNotEqual(a, b, 'olika fakta måste ge olika nycklar')

    def test_identical_content_same_key(self):
        """Identiska fakta ska dedupas — det är hela poängen med nyckeln."""
        a = self.coworker._normalize_concept_key(
            'Kunden föredrar fakturering via e-post.', 'personal')
        b = self.coworker._normalize_concept_key(
            '  KUNDEN FÖREDRAR FAKTURERING VIA E-POST.  ', 'personal')
        self.assertEqual(a, b, 'samma fakta (normaliserad) = samma nyckel')

    def test_summary_length_does_not_matter(self):
        """4.7/5.2: den gamla nyckeln berodde på LÄNGDEN.

        Två helt olika fakta med samma längd fick samma nyckel. Det ska inte
        kunna hända nu.
        """
        a = self.coworker._normalize_concept_key('ABCD', 'personal')
        b = self.coworker._normalize_concept_key('WXYZ', 'personal')
        self.assertNotEqual(a, b)

    def test_scope_separates_keys(self):
        """Samma fakta i olika scope är olika koncept."""
        a = self.coworker._normalize_concept_key('Samma text.', 'personal')
        b = self.coworker._normalize_concept_key('Samma text.', 'company')
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith('personal.'))

    def test_llm_key_used_when_valid(self):
        """5.3: en användbar LLM-nyckel går före hashen."""
        key = self.coworker._normalize_concept_key(
            'Kunden föredrar fakturering via e-post.', 'personal',
            llm_key='kund.fakturering.epost')
        self.assertEqual(key, 'personal.kund.fakturering.epost')

    def test_invalid_llm_key_falls_back_to_hash(self):
        """5.5: en oanvändbar LLM-nyckel ska INTE bli en nyckel."""
        for bad in (None, '', '  ', 'a', '###', 42):
            key = self.coworker._normalize_concept_key(
                'Kunden föredrar fakturering via e-post.', 'personal',
                llm_key=bad)
            self.assertTrue(key.startswith('personal.fakta.'),
                            'skräpnyckeln %r skulle gett hash-fallback' % (bad,))

    def test_sanitize_handles_messy_llm_output(self):
        """5.5: LLM:er skickar skräp — normalisera, krascha inte."""
        self.assertEqual(
            self.coworker._sanitize_concept_key('Kund Fakturering E-post'),
            'kund.fakturering.e-post')
        self.assertEqual(
            self.coworker._sanitize_concept_key('kund...fakturering'),
            'kund.fakturering')
        self.assertEqual(
            self.coworker._sanitize_concept_key('.kund.fakturering.epost.'),
            'kund.fakturering.epost')
        self.assertIsNone(self.coworker._sanitize_concept_key('x'))
        self.assertIsNone(self.coworker._sanitize_concept_key(None))
        self.assertIsNone(self.coworker._sanitize_concept_key(12345))

    def test_sanitize_bounds_length(self):
        """5.5: en nyckel får inte spränga indexet."""
        key = self.coworker._sanitize_concept_key('k' * 500)
        self.assertLessEqual(len(key), 120)


@tagged('okf', 'learning', 'post_install', '-at_install')
class TestLearnFromSummary(common.TransactionCase):
    """5.1 + 5.9: lärandet läser eftermälet, inte råsvansen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env.ref('base.user_admin')

    def _make_session(self, n_lines):
        session = self.Session.create({
            'name': 'learn-test', 'status': 'active',
            'user_id': self.user.id,
        })
        for i in range(n_lines):
            self.Line.create({
                'session_id': session.id, 'sequence': i + 1,
                'role': 'user' if i % 2 == 0 else 'assistant',
                'content': 'Rad %d med innehåll.' % (i + 1),
            })
        return session

    def test_learning_uses_summary_not_raw_tail(self):
        """5.9: en 200-radssession ska lära ur eftermälet.

        Råsvansen `lines[-40:]` tappar allt som hände i början av en lång
        session. Testet bevisar att det är EFTERMÄLET som skickas till
        reflektionen: vi sätter ett känt innehåll på summary och kontrollerar
        att det — och inte radtexten — når `_extract_concepts_from_conversation`.
        """
        session = self._make_session(n_lines=200)
        session.summary = '### Beslut\nEftermälets unika text.'
        session.summary_message_count = 200

        seen = {}

        def capture(conversation):
            seen['conversation'] = conversation
            return []

        coworker = self.env['ai.coworker'].create({
            'name': 'learn-cw', 'learning': 'active'})
        with patch.object(type(coworker),
                          '_extract_concepts_from_conversation',
                          side_effect=capture):
            coworker._learn_from_session(session)

        self.assertIn('Eftermälets unika text', seen.get('conversation', ''),
                      'eftermälet ska vara underlaget för lärandet')

    def test_learning_skips_session_without_summary(self):
        """5.1: ingen råsvans när eftermälet saknas — hoppa över i stället.

        En för kort session ger inget eftermäle. Då ska lärandet INTE falla
        tillbaka på att läsa de sista raderna: det vore tyst lärande på fel
        underlag.
        """
        session = self._make_session(n_lines=2)  # under tröskeln
        coworker = self.env['ai.coworker'].create({
            'name': 'learn-cw2', 'learning': 'active'})

        with patch.object(type(coworker),
                          '_extract_concepts_from_conversation') as extract:
            written = coworker._learn_from_session(session)

        self.assertEqual(written, 0)
        self.assertFalse(extract.called,
                         'reflektionen ska inte anropas utan eftermäle')

    def test_learning_skips_when_not_active(self):
        """Lärande är avstängt om learning != 'active'."""
        session = self._make_session(n_lines=6)
        session.summary = '### Beslut\nNågot.'
        coworker = self.env['ai.coworker'].create({
            'name': 'learn-cw3', 'learning': 'passive'})
        self.assertEqual(coworker._learn_from_session(session), 0)
