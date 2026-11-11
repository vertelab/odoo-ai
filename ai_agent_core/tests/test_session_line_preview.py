# -*- coding: utf-8 -*-
"""Listvyns förhandsvisning (`content_preview`) — session-line-compact-list.

VARFÖR: `content` är ett `Text`-fält, och Odoo 18:s list-renderare ger
text-fält `white-space: pre-wrap`. Ett meddelande på 2 000 tecken blev 30–40
visuella rader, så listan gick inte att överblicka. Cellens `text-truncate()`
biter inte mot `pre-wrap` — klippningen måste ske i värdet.

Dessa tester bevisar:
  1. lång text klipps till PREVIEW_LENGTH och slutar med `…`
  2. flerradig text visar FÖRSTA raden (klipp vid radbrytning först)
  3. tomt/blanksteg-only `content` ger tom sträng
  4. kort text klipps inte
  5. `content` är oförändrat — förhandsvisningen är en projektion
  6. sökning går fortfarande mot `content`, inte mot förhandsvisningen
"""

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.models.ai_session_line import PREVIEW_LENGTH
from ._test_users import create_user


@tagged('session_line_preview', 'post_install', '-at_install')
class TestSessionLinePreview(common.TransactionCase):
    """`ai.coworker.session.line.content_preview` — en rad per meddelande."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env['ai.coworker.session.line']
        cls.Session = cls.env['ai.coworker.session']
        cls.user = create_user(cls.env, name='Line Preview User', login='line_preview@example.com', email='line_preview@example.com')
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Line Preview Coworker',
            'status': 'active',
        })
        cls.session = cls.Session.create({
            'coworker_id': cls.coworker.id,
            'status': 'active',
            'user_id': cls.user.id,
            'name': 'preview test',
        })

    def _line(self, content):
        """Skapa en rad med givet innehåll; sekvensen sköts av modellen."""
        return self.Line.create({
            'session_id': self.session.id,
            'role': 'assistant',
            'content': content,
            'sequence': self.coworker._next_session_sequence(self.session),
        })

    # ── 1. Klippningen ────────────────────────────────────────────────

    def test_long_single_line_is_cut(self):
        """2 000 tecken utan radbrytning → exakt PREVIEW_LENGTH med `…`."""
        line = self._line('a' * 2000)
        limit = PREVIEW_LENGTH
        self.assertEqual(len(line.content_preview), limit)
        self.assertTrue(line.content_preview.endswith('…'),
                        'en klippt förhandsvisning ska sluta med …')

    def test_multiline_shows_first_line_only(self):
        """Första raden vinner — inte "första 120 tecken inkl. radbrytningar"."""
        line = self._line('Hej\n\nDetta är rad tre')
        self.assertEqual(line.content_preview, 'Hej')
        self.assertNotIn('\n', line.content_preview)

    def test_empty_content_gives_empty_preview(self):
        """Tomt `content` → tom sträng, ingen krasch."""
        line = self._line(False)
        self.assertEqual(line.content_preview, '')

    def test_whitespace_only_gives_empty_preview(self):
        """En rad med bara blanksteg ska se tom ut, inte som ett mellanslag."""
        line = self._line('   \n  \t ')
        self.assertEqual(line.content_preview, '')

    def test_short_text_is_not_cut(self):
        """Kort text är identisk med `content`, ingen `…` läggs till."""
        line = self._line('Kort svar.')
        self.assertEqual(line.content_preview, 'Kort svar.')
        self.assertNotIn('…', line.content_preview)

    # ── 2. Förhandsvisningen är en projektion ─────────────────────────

    def test_content_is_unchanged(self):
        """`content` lagras i sin helhet — append-only-kravet gäller."""
        text = 'x' * 500
        line = self._line(text)
        self.assertEqual(line.content, text)
        self.assertEqual(len(line.content), 500)

    def test_search_still_uses_content(self):
        """Text längre ned i meddelandet hittas av sökningen på `content`."""
        word = 'kanelbulle'
        filler = '\n'.join('rad %d' % i for i in range(1, 20))
        line = self._line('%s\n%s' % (filler, word))
        # Förhandsvisningen har bara första raden — ordet syns inte där.
        self.assertNotIn(word, line.content_preview)
        # Men sökningen mot `content` hittar raden.
        found = self.Line.search([
            ('session_id', '=', self.session.id),
            ('content', 'ilike', word),
        ])
        self.assertIn(line, found,
                      'sökning ska gå mot content, inte mot förhandsvisningen')
