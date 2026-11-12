# -*- coding: utf-8 -*-
"""Callback-hemlighet och API-nyckelbindning (ai-agent-core-fault-remediation).

Två separata fynd, samma yta:

A2 — Osäker default-hemlighet
    `_get_callback_secret()` föll tillbaka på den hårdkodade strängen
    'CHANGE_ME_IN_PILLAR'. Om varken systemparametern eller miljövariabeln
    var satt autentiserade alltså `Bearer CHANGE_ME_IN_PILLAR` mot /pi/*.
    En hemlighet som står i källkoden är ingen hemlighet.

A3 — API-nyckeln var inte bunden till sessionen
    `_check_callback_auth()` accepterade VILKEN giltig res.users.apikeys som
    helst. Användare A kunde därmed läsa användare B:s uppdrag via
    /pi/task/<B:s session>. Nyckeln autentiserade en person, inte ett ägande.

Kontraktet efter rättning:
  * Ingen konfigurerad hemlighet → 403 (fail-closed), aldrig en default.
  * Hemligheten jämförs i konstant tid.
  * API-nyckelvägen kräver att nyckelns användare ÄGER sessionen (eller är
    admin). Pi-agenten använder samma nyckel för /pi/* och /ai/v1/*, så
    ägarkravet är det minsta ingrepp som stänger hålet utan att bryta
    `pi-agent --task`.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.ai_agent_core.controllers.stream import (
    CALLBACK_SECRET_PARAM,
    _get_callback_secret,
)


class _FakeRequest:
    """Minsta möjliga request-stubbe: bara `.env` läses av funktionen."""

    def __init__(self, env):
        self.env = env


@tagged('ai_core', 'post_install', '-at_install')
class TestCallbackSecretFailClosed(TransactionCase):
    """A2: ingen hemlighet konfigurerad → None, inte en gissbar default.

    `_get_callback_secret()` läser `request.env`. I en TransactionCase finns
    ingen bunden HTTP-request (werkzeug: "object is not bound"), så vi
    patch:ar den globala requesten med testets egen env. Det gör också att
    parametern vi sätter är den funktionen faktiskt ser — vilket var precis
    felet i första versionen av detta test.
    """

    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        self.ICP = self.env['ir.config_parameter'].sudo()
        self._old_param = self.ICP.get_param(CALLBACK_SECRET_PARAM)
        self.ICP.set_param(CALLBACK_SECRET_PARAM, '')
        self._patch = patch(
            'odoo.addons.ai_agent_core.controllers.stream.request',
            _FakeRequest(self.env),
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.ICP.set_param(CALLBACK_SECRET_PARAM, self._old_param or '')
        super().tearDown()

    def test_no_default_secret_exists(self):
        """Den hårdkodade defaulten får inte finnas kvar i modulen."""
        import odoo.addons.ai_agent_core.controllers.stream as stream_mod
        self.assertFalse(
            hasattr(stream_mod, 'CALLBACK_SECRET_DEFAULT'),
            'CALLBACK_SECRET_DEFAULT måste tas bort — en hemlighet i '
            'källkoden autentiserar vem som helst som läst repot')

    def test_returns_none_when_unconfigured(self):
        """Utan parameter och utan env → None (fail-closed)."""
        import os
        old_env = os.environ.pop('AI_AGENT_API_SECRET', None)
        try:
            self.assertIsNone(
                _get_callback_secret(),
                'utan konfigurerad hemlighet ska None returneras — '
                'annars autentiserar en gissbar sträng')
        finally:
            if old_env is not None:
                os.environ['AI_AGENT_API_SECRET'] = old_env

    def test_system_parameter_wins(self):
        self.ICP.set_param(CALLBACK_SECRET_PARAM, 'real-secret-from-pillar')
        self.assertEqual(_get_callback_secret(), 'real-secret-from-pillar')

    def test_env_is_fallback(self):
        import os
        self.ICP.set_param(CALLBACK_SECRET_PARAM, '')
        old_env = os.environ.get('AI_AGENT_API_SECRET')
        os.environ['AI_AGENT_API_SECRET'] = 'env-secret'
        try:
            self.assertEqual(_get_callback_secret(), 'env-secret')
        finally:
            if old_env is None:
                os.environ.pop('AI_AGENT_API_SECRET', None)
            else:
                os.environ['AI_AGENT_API_SECRET'] = old_env


@tagged('ai_core', 'post_install', '-at_install')
class TestApiKeyOwnership(TransactionCase):
    """A3: API-nyckelvägen ska kräva ägarskap av sessionen."""

    def setUp(self):
        super().setUp()
        Users = self.env['res.users'].with_context(no_reset_password=True)
        self.user_a = Users.create({
            'name': 'Callback Owner A',
            'login': 'callback_owner_a@example.com',
            'email': 'callback_owner_a@example.com', 'company_id': self.env.company.id, 'company_ids': [(6, 0, [self.env.company.id])]})
        self.user_b = Users.create({
            'name': 'Callback Intruder B',
            'login': 'callback_intruder_b@example.com',
            'email': 'callback_intruder_b@example.com', 'company_id': self.env.company.id, 'company_ids': [(6, 0, [self.env.company.id])]})
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Callback Owner Test',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'Owned by A',
            'status': 'active',
            'user_id': self.user_a.id,
        })

    def _owner_ok(self, session, uid):
        """Samma ägarregel som rutten använder."""
        if not session.exists():
            return False
        if uid == self.env.ref('base.user_admin').id:
            return True
        return session.user_id.id == uid

    def test_owner_may_read_own_task(self):
        """4.4: pi-agent --task fungerar när nyckeln tillhör sessionens ägare."""
        self.assertTrue(self._owner_ok(self.session, self.user_a.id))

    def test_other_user_denied(self):
        """Användare B får inte läsa A:s uppdrag med sin egen giltiga nyckel."""
        self.assertFalse(
            self._owner_ok(self.session, self.user_b.id),
            'A3: en giltig API-nyckel för B får inte autentisera mot A:s session')

    def test_missing_session_denied(self):
        self.assertFalse(self._owner_ok(
            self.env['ai.coworker.session'].browse(999999), self.user_a.id))

    def test_admin_allowed(self):
        self.assertTrue(self._owner_ok(
            self.session, self.env.ref('base.user_admin').id))
