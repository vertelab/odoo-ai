# -*- coding: utf-8 -*-
"""Publika /ai/*-rutter: ägarkontroll och åtkomstprövning (A4/A5/A8).

Fynden, i klartext:

A4 — Oautentiserade publika rutter
    `thread_get`, `thread_rename`, `thread_delete`, `memory_archive`,
    `session_documents` och `upload_document` slog upp sessionen med
    `sudo()` och jämförde aldrig mot användaren. Vem som helst som kunde
    gissa ett id kunde läsa, döpa om, radera eller ladda upp dokument till
    någon annans tråd. `upload_document` var dessutom en oautentiserad
    filläsare: den extraherade text ur en uppladdad fil och skapade en
    `ir.attachment` med `sudo()` kopplad till ett valfritt `session_id`.

    `skill_get` returnerade `recipe_text` (hela instruktionsreceptet) utan
    prövning. `powerbox_run` startade en LLM-körning för vilken coworker som
    helst — dvs. vem som helst kunde bränna någon annans budget.
    `learn_command` skrev till identiteten för ett godtyckligt coworker_id.

A5 — `_quest_is_accessible` kan vara None
    Modulnivå `except Exception: _quest_is_accessible = None`. Anropet
    oskyddat → TypeError → HTTP 500 i stället för 403.

A8 — `_quest_is_accessible` ignorerade `user_ids`
    Prövade bara `group_ids`-korsning. En coworker delad med en enskild
    användare blev otillgänglig för den användaren.

Testerna prövar beslutslogiken (inte HTTP-lagret): rutterna är publika och
kräver ingen API-nyckel, men det som ska bevakas är VEM som släpps in.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.controllers.stream import _require_session_owner
from odoo.addons.ai_agent_core.models.ai_coworker import _quest_is_accessible


class _FakeRequest:
    """Minsta request-stubbe: bara `.env` läses av funktionerna."""

    def __init__(self, env):
        self.env = env


@tagged('ai_core', 'post_install', '-at_install')
class TestQuestAccessible(TransactionCase):
    """A8: både group_ids och user_ids ska ge åtkomst."""

    def setUp(self):
        super().setUp()
        Users = self.env['res.users'].with_context(no_reset_password=True)
        self.user_in = Users.create({
            'name': 'Quest User In', 'login': 'quest_user_in@example.com',
        })
        self.user_out = Users.create({
            'name': 'Quest User Out', 'login': 'quest_user_out@example.com',
        })
        self.group = self.env['res.groups'].create({
            'name': 'Quest Access Test Group',
        })
        self.group.users = [(4, self.user_in.id)]
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Quest Access Test',
        })

    def test_open_coworker_allows_everyone(self):
        """Inga grupper, inga användare → öppen (oförändrat beteende)."""
        self.assertTrue(_quest_is_accessible(self.coworker, self.user_out))

    def test_group_member_allowed(self):
        self.coworker.group_ids = [(4, self.group.id)]
        self.assertTrue(_quest_is_accessible(self.coworker, self.user_in))

    def test_non_group_member_denied(self):
        self.coworker.group_ids = [(4, self.group.id)]
        self.assertFalse(_quest_is_accessible(self.coworker, self.user_out))

    def test_explicit_user_allowed_without_group(self):
        """A8: user_ids ska räcka — annars var fältet verkningslöst."""
        self.coworker.user_ids = [(4, self.user_in.id)]
        self.assertTrue(
            _quest_is_accessible(self.coworker, self.user_in),
            'en användare som står i user_ids måste släppas in')

    def test_user_not_listed_denied(self):
        self.coworker.user_ids = [(4, self.user_in.id)]
        self.assertFalse(_quest_is_accessible(self.coworker, self.user_out))

    def test_admin_always_allowed(self):
        self.coworker.group_ids = [(4, self.group.id)]
        admin = self.env.ref('base.user_admin')
        self.assertTrue(_quest_is_accessible(self.coworker, admin))


@tagged('ai_core', 'post_install', '-at_install')
class TestSessionOwnerLookup(TransactionCase):
    """5.1: `_require_session_owner` — tre fallen.

    Funktionen läser `request.env.user`. Vi patch:ar den globala requesten
    med en stubbe vars env har rätt användare per fall.
    """

    def setUp(self):
        super().setUp()
        Users = self.env['res.users'].with_context(no_reset_password=True)
        self.owner = Users.create({
            'name': 'Thread Owner', 'login': 'thread_owner@example.com',
        })
        self.other = Users.create({
            'name': 'Thread Other', 'login': 'thread_other@example.com',
        })
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Owner Lookup Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Owned thread',
            'status': 'active',
            'user_id': self.owner.id,
        })

    def _call(self, user, session_id):
        """Anropa `_require_session_owner` som `user`."""
        env = self.env(user=user.id)
        with patch(
            'odoo.addons.ai_agent_core.controllers.stream.request',
            _FakeRequest(env),
        ):
            return _require_session_owner(session_id)

    def test_owner_allowed(self):
        session, denied = self._call(self.owner, self.session.id)
        self.assertIsNone(denied)
        self.assertEqual(session.id, self.session.id)

    def test_other_user_forbidden(self):
        session, denied = self._call(self.other, self.session.id)
        self.assertIsNone(session)
        self.assertIsNotNone(denied)
        self.assertEqual(denied.status_code, 403)

    def test_unknown_session_404(self):
        session, denied = self._call(self.owner, 999999)
        self.assertIsNone(session)
        self.assertIsNotNone(denied)
        self.assertEqual(denied.status_code, 404)

    def test_admin_allowed(self):
        admin = self.env.ref('base.user_admin')
        session, denied = self._call(admin, self.session.id)
        self.assertIsNone(denied)
        self.assertEqual(session.id, self.session.id)


@tagged('ai_core', 'post_install', '-at_install')
class TestRoutesUseOwnerCheck(TransactionCase):
    """Strukturell grind: rutterna får inte slå upp med sudo() och köra vidare.

    Detta är testet som hade fångat A4. Ett beteendetest hade bara visat att
    EN rutt nekade; detta visar att ägarkontrollen finns på ALLA de rutter
    som A4 gällde — och att den inte kan tas bort tyst igen.
    """

    # Rutter som måste anropa _require_session_owner.
    ROUTES = (
        'thread_get',
        'thread_rename',
        'thread_delete',
        'session_documents',
        'upload_document',
        'memory_archive',
    )

    def test_all_listed_routes_call_owner_check(self):
        import ast
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        missing = []
        for name in self.ROUTES:
            func = getattr(stream_mod.AIStreamController, name, None)
            self.assertIsNotNone(func, f'{name} saknas på controller-klassen')
            src = textwrap.dedent(inspect.getsource(func))
            if '_require_session_owner' not in src:
                missing.append(name)

        self.assertEqual(
            missing, [],
            'Dessa publika rutter saknar ägarkontroll: ' + ', '.join(missing))

    def test_upload_document_checks_before_creating_attachment(self):
        """5.3: kontrollen måste ligga FÖRE ir.attachment.create()."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(inspect.getsource(
            stream_mod.AIStreamController.upload_document))
        check_pos = src.find('_require_session_owner')
        att_pos = src.find("'ir.attachment'")
        self.assertNotEqual(check_pos, -1, 'ägarkontroll saknas')
        self.assertNotEqual(att_pos, -1, 'ir.attachment skapas inte längre?')
        self.assertLess(
            check_pos, att_pos,
            'ägarkontrollen måste ske innan bilagan skapas')

    def test_powerbox_checks_access_before_run(self):
        """5.5: ingen LLM-körning får startas för en otillåten coworker."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(inspect.getsource(
            stream_mod.AIStreamController.powerbox_run))
        check_pos = src.find('_quest_is_accessible')
        run_pos = src.find('quest.powerbox(')
        self.assertNotEqual(check_pos, -1, 'åtkomstprövning saknas')
        self.assertNotEqual(run_pos, -1, 'quest.powerbox() anropas inte längre?')
        self.assertLess(
            check_pos, run_pos,
            'åtkomstprövningen måste ske före quest.powerbox()')

    def test_skill_get_checks_groups(self):
        """5.4: recipe_text får inte läcka för en användare utan gruppen."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(inspect.getsource(
            stream_mod.AIStreamController.skill_get))
        self.assertIn('group_ids', src,
                      'skill_get måste pröva ai.skill.group_ids')

    def test_learn_command_binds_to_own_companion(self):
        """5.6: godtyckligt coworker_id får inte skriva till annans identitet."""
        import inspect
        import textwrap
        from odoo.addons.ai_agent_core.controllers import stream as stream_mod

        src = textwrap.dedent(inspect.getsource(
            stream_mod.AIStreamController.learn_command))
        self.assertIn('personal_coworker_id', src,
                      'learn_command måste binda till användarens egen companion')
        self.assertIn('403', src, 'fel companion måste nekas med 403')


@tagged('ai_core', 'post_install', '-at_install')
class TestWebUiThreadFlowStillWorks(TransactionCase):
    """19.5: web-UI:t ska kunna lista, byta namn på och radera EGNA trådar.

    `chat_template.html` använder `/ai/threads` (GET/POST),
    `/ai/threads/<id>` (PUT för namn, DELETE för radering). Ägarkravet får
    inte bryta dessa för den som äger tråden.
    """

    def setUp(self):
        super().setUp()
        Users = self.env['res.users'].with_context(no_reset_password=True)
        self.owner = Users.create({
            'name': 'WebUI Owner', 'login': 'webui_owner@example.com',
        })
        self.coworker = self.env['ai.coworker'].create({
            'name': 'WebUI Flow Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Min tråd',
            'status': 'active',
            'user_id': self.owner.id,
        })

    def _as_owner(self):
        env = self.env(user=self.owner.id)
        return patch(
            'odoo.addons.ai_agent_core.controllers.stream.request',
            _FakeRequest(env))

    def test_owner_can_open_own_thread(self):
        with self._as_owner():
            session, denied = _require_session_owner(self.session.id)
        self.assertIsNone(denied)
        self.assertEqual(session.name, 'Min tråd')

    def test_owner_can_rename_own_thread(self):
        """PUT-vägen: ägaren får skriva namnet."""
        with self._as_owner():
            session, denied = _require_session_owner(self.session.id)
            self.assertIsNone(denied)
            session.name = 'Nytt namn'[:200]
        self.assertEqual(self.session.name, 'Nytt namn')

    def test_owner_can_delete_own_thread(self):
        """DELETE-vägen: ägaren får mjuk-radera."""
        with self._as_owner():
            session, denied = _require_session_owner(self.session.id)
            self.assertIsNone(denied)
            session.active = False
        self.assertFalse(self.session.active)

    def test_other_user_cannot_touch_the_thread(self):
        Users = self.env['res.users'].with_context(no_reset_password=True)
        other = Users.create({
            'name': 'WebUI Other', 'login': 'webui_other@example.com',
        })
        env = self.env(user=other.id)
        with patch(
            'odoo.addons.ai_agent_core.controllers.stream.request',
            _FakeRequest(env),
        ):
            _session, denied = _require_session_owner(self.session.id)
        self.assertIsNotNone(denied, 'annan användare ska nekas')
        self.assertEqual(denied.status_code, 403)
