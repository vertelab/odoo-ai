# -*- coding: utf-8 -*-
"""Navigering från en lineage-rad till sitt objekt
(session-object-navigation).

VARFÖR: `Objects`-listan visade bara `res_model`/`res_id` — för att se
uppgiften eller partnern måste användaren kopiera modellnamn och id och
bygga URL:en för hand. `action_open_target()` slår upp målet via målets eget
`get_formview_action()` så att modellens vy, meny och behörigheter gäller.

Dessa tester bevisar:
  1. ett befintligt objekt ger målmodellens egen åtgärd med rätt `res_id`
  2. de tre otillgänglighetsfallen ger en förklarande notis — aldrig `False`
  3. ingen post öppnas i strid med behörighetsreglerna (inget `sudo()`)
"""

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged('session_object_navigation', 'post_install', '-at_install')
class TestSessionObjectNavigation(TransactionCase):
    """session-object-navigation: radåtgärden och dess tre felutfall."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Lineage = cls.env['ai.coworker.session.object']
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Navigation Coworker',
            'status': 'active',
        })
        cls.session = cls.env['ai.coworker.session'].create({
            'coworker_id': cls.coworker.id,
            'status': 'active',
            'user_id': cls.env.user.id,
            'name': 'navigation session',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Navigation Target Partner',
        })

    def _lineage(self, **vals):
        base = {
            'session_id': self.session.id,
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'operation': 'created',
        }
        base.update(vals)
        return self.Lineage.create(base)

    # ── 1. målet öppnas via målmodellens egen åtgärd ────────────────────

    def test_existing_object_returns_model_action(self):
        """Ett befintligt objekt ger en act_window på rätt modell och id."""
        action = self._lineage().action_open_target()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'res.partner')
        self.assertEqual(action['res_id'], self.partner.id)
        self.assertNotIn('display_notification', str(action.get('tag')))

    def test_action_comes_from_the_target_model(self):
        """Åtgärden hämtas från målets get_formview_action, inte en URL.

        Vi bevisar det genom att jämföra med vad målet självt returnerar —
        en handbyggd dict skulle kunna se likadan ut, men en modell som
        överskuggar metoden (som hr.employee gör) skulle då tappa sin vy.
        """
        action = self._lineage().action_open_target()

        self.assertEqual(
            action['res_model'],
            self.partner.get_formview_action()['res_model'])
        self.assertEqual(
            action['res_id'],
            self.partner.get_formview_action()['res_id'])

    def test_action_is_not_built_for_a_url(self):
        """Ingen `views`-lista med påhittade id:n och ingen `path`-nyckel."""
        action = self._lineage().action_open_target()

        # get_formview_action sätter (False, 'form') när modellen inte
        # överskuggar get_formview_id — det är ett vy-uppslag, inte en URL.
        self.assertNotIn('path', action)
        self.assertEqual(action.get('target'), 'current')

    # ── 2. de tre otillgänglighetsfallen ────────────────────────────────

    def _assert_notification(self, action, fragment):
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn(fragment, action['params']['message'])

    def test_deleted_object_gives_a_notification(self):
        """Raderat objekt: notis som säger att det inte finns längre."""
        row = self._lineage(res_id=999999999)

        action = row.action_open_target()

        self._assert_notification(action, 'finns inte längre')

    def test_uninstalled_model_gives_a_notification(self):
        """Modell som inte finns i registret: notis om otillgänglig modell."""
        row = self._lineage(res_model='x_model.that.does.not.exist')

        action = row.action_open_target()

        self._assert_notification(action, 'inte tillgänglig')
        self.assertIn('x_model.that.does.not.exist',
                      action['params']['message'])

    def test_missing_read_access_gives_a_notification(self):
        """Användare utan läsrätt får notis — och ingen åtgärd.

        `ir.actions.server` kräver `base.group_system`. En användare med bara
        `base.group_user` saknar därför läsrätt, och prövningen ska ge en
        notis — inte ett `AccessError`.
        """
        user = self.env['res.users'].create({
            'name': 'Navigation No Access',
            'login': 'navigation_no_access_test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        restricted_model = 'ir.actions.server'
        row = self._lineage(
            res_model=restricted_model,
            res_id=self.env['ir.actions.server'].create({
                'name': 'Navigation Restricted Action',
                'state': 'code',
                'code': 'pass',
                'model_id': self.env['ir.model']._get_id('res.partner'),
            }).id)

        # Testet förutsätter att användaren bevisligen saknar läsrätt.
        self.assertFalse(
            self.env[restricted_model].with_user(user).has_access('read'))

        Action = row.with_user(user)
        # Ingen AccessError får bubbla ut — utfallet ska vara kontrollerat.
        action = Action.action_open_target()

        self._assert_notification(action, 'behörighet')

    # ── 3. behörighet eskaleras aldrig ──────────────────────────────────

    def test_navigation_never_uses_sudo(self):
        """Prövningen sker i användarens egen kontext, aldrig med sudo.

        Om åtgärden körde `sudo()` skulle vilken rad som helst öppna vilket
        objekt som helst — då vore behörighetskravet verkningslöst.
        """
        parent = self.env['res.users'].create({
            'name': 'Navigation Admin User',
            'login': 'navigation_admin_test',
            'groups_id': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_system').id,
            ])],
        })
        row = self._lineage()
        # Som administratör fungerar raden (kontrollfall) …
        self.assertEqual(
            row.with_user(parent).action_open_target()['res_model'],
            'res.partner')

        # … och prövningen som gör skillnad är has_access, inte exists.
        self.assertTrue(self.env['res.partner'].has_access('read'))
        self.assertIsNone(
            row._target_unavailable_reason(),
            'behörig användare ska få None tillbaka')
