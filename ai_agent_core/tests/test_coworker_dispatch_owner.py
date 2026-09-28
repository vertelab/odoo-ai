# -*- coding: utf-8 -*-
"""coworker-dispatch-owner — ägaren för automatiska körningar.

Fyndet i drift 2026-09-18: 29 av 29 aktiva coworkers saknade
`chat_user_id`, 0 bot-users existerade, och bron från session till
personligt minne kunde därför inte skriva något.

Orsaken var fyra sammanlänkade fel. Dessa tester täcker varje led.
"""

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestChatUserLink(TransactionCase):
    """Led 1 + 2 — bot-usern kopplas till coworkern, inte bara init-typen."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']

    def _make_coworker(self, name):
        return self.Coworker.create({'name': name, 'status': 'active'})

    def test_both_fields_are_set(self):
        """`_ensure_chat_user()` sätter BÅDA fälten.

        Att bara sätta init-typens fält lämnade coworkern utan ägare —
        och `_resolve_dispatch_user()` läser coworkerns.
        """
        coworker = self._make_coworker('Ägar Test Ett')
        init = coworker.init_type_ids.filtered(
            lambda r: r.init_type == 'chat')[:1]
        self.assertTrue(init, 'chat-init_type ska finnas')
        init._ensure_chat_user()

        self.assertTrue(init.chat_user_id, 'init-typen ska ha en bot-user')
        self.assertTrue(
            coworker.chat_user_id, 'coworkern ska ha en ägare')
        self.assertEqual(
            coworker.chat_user_id, init.chat_user_id,
            'båda fälten ska peka på samma användare')

    def test_existing_bot_user_reused(self):
        """Andra körningen skapar ingen dubblett."""
        coworker = self._make_coworker('Ägar Test Två')
        init = coworker.init_type_ids.filtered(
            lambda r: r.init_type == 'chat')[:1]
        init._ensure_chat_user()
        first = init.chat_user_id

        init._ensure_chat_user()
        self.assertEqual(init.chat_user_id, first)

        login = 'bot_' + coworker.name.lower().replace(' ', '_')
        users = self.env['res.users'].search([('login', '=', login)])
        self.assertEqual(len(users), 1, 'ingen dubblett ska skapas')

    def test_bot_user_login_is_derived(self):
        """Bot-userns login följer coworkerns namn."""
        coworker = self._make_coworker('Ägar Test Tre')
        init = coworker.init_type_ids.filtered(
            lambda r: r.init_type == 'chat')[:1]
        init._ensure_chat_user()
        self.assertEqual(
            coworker.chat_user_id.login,
            'bot_' + coworker.name.lower().replace(' ', '_'))


class TestOwnerIsWritable(TransactionCase):
    """D3 — ägaren är ett beslut, inte en artefakt."""

    def test_field_is_not_readonly(self):
        """Fältet måste gå att ändra."""
        field = self.env['ai.coworker']._fields['chat_user_id']
        self.assertFalse(
            field.readonly,
            'chat_user_id ska vara redigerbart — en ägare är ett beslut')

    def test_owner_can_be_changed(self):
        """En människa kan sättas som ägare."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Ägar Test Fyra', 'status': 'active',
        })
        human = self.env.ref('base.user_admin')
        coworker.chat_user_id = human.id
        self.assertEqual(coworker.chat_user_id, human)


class TestDispatchOwnerResolution(TransactionCase):
    """D2 — upplösningen hittar ägaren."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']
        self.root = self.env.ref('base.user_root')

    def test_configured_owner_wins(self):
        """En konfigurerad ägare returneras för automatisk körning."""
        owner = self.env.ref('base.user_admin')
        coworker = self.Coworker.create({
            'name': 'Dispatch Test Ett', 'status': 'active',
            'chat_user_id': owner.id,
        })
        user = coworker._resolve_dispatch_user(init_type='cron')
        self.assertEqual(user, owner)

    def test_missing_owner_raises_with_field_name(self):
        """Utan ägare kastas ett fel som namnger fältet."""
        coworker = self.Coworker.create({
            'name': 'Dispatch Test Två', 'status': 'active',
        })
        coworker.chat_user_id = False
        # Töm init-typernas ägare också — annars hittar fallbacken dem
        coworker.init_type_ids.write({'chat_user_id': False, 'enabled': False})

        with self.assertRaises(ValidationError) as ctx:
            coworker._resolve_dispatch_user(init_type='cron')
        self.assertIn('Ägare', str(ctx.exception))

    def test_systemuser_still_refused(self):
        """Systemuser-spärren är orörd — den är korrekt."""
        coworker = self.Coworker.create({
            'name': 'Dispatch Test Tre', 'status': 'active',
            'chat_user_id': self.root.id,
        })
        with self.assertRaises(ValidationError) as ctx:
            coworker._resolve_dispatch_user(init_type='cron')
        self.assertIn('systemuser', str(ctx.exception))


class TestRepairMissingChatUsers(TransactionCase):
    """D4 — reparationen ger aktiva coworkers en ägare."""

    def setUp(self):
        super().setUp()
        self.Coworker = self.env['ai.coworker']

    def test_repair_gives_owners(self):
        """Coworkers utan ägare får en."""
        coworkers = self.env['ai.coworker'].create([
            {'name': 'Reparera Ett', 'status': 'active'},
            {'name': 'Reparera Två', 'status': 'active'},
        ])
        coworkers.write({'chat_user_id': False})

        repaired = self.Coworker._repair_missing_chat_users()
        self.assertGreaterEqual(repaired, 2)

        for coworker in coworkers:
            self.assertTrue(
                coworker.chat_user_id,
                f'{coworker.name} ska ha fått en ägare')

    def test_repair_is_idempotent(self):
        """Andra körningen reparerar 0."""
        self.env['ai.coworker'].create({
            'name': 'Reparera Tre', 'status': 'active',
        })
        self.Coworker._repair_missing_chat_users()
        second = self.Coworker._repair_missing_chat_users()
        self.assertEqual(second, 0)

    def test_inactive_coworkers_untouched(self):
        """Bara AKTIVA coworkers repareras."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Reparera Inaktiv', 'status': 'error',
        })
        coworker.write({'chat_user_id': False})
        self.Coworker._repair_missing_chat_users()
        self.assertFalse(
            coworker.chat_user_id,
            'en inaktiv coworker ska inte få en ägare')


class TestRepairedOwnerEnablesBridge(TransactionCase):
    """Ändringens mål: bron ska kunna använda ägaren."""

    def test_repair_then_bridge_writes(self):
        """Efter reparation kan bron skriva till rätt användare."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Bro Efter Reparation', 'status': 'active',
        })
        coworker.write({'chat_user_id': False})
        self.env['ai.coworker']._repair_missing_chat_users()

        self.assertTrue(coworker.chat_user_id)
        user = coworker._resolve_dispatch_user(init_type='cron')
        self.assertNotEqual(user, self.env.ref('base.user_root'))
        self.assertTrue(user.login.startswith('bot_'))
