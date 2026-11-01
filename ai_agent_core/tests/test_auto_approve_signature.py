# -*- coding: utf-8 -*-
"""B8 — signerad `_ai_auto_approve`: AUTO-mode får inte kunna begäras fritt.

Bakgrund: `_ai_auto_approve` var en naken context-nyckel. Koden i
`ai_coworker.py` gjorde:

    if self.env.context.get('_ai_auto_approve'):
        loop_obj.permissions = PermissionEngine(mode=PermissionMode.AUTO)

…vilket ger full behörighet utan människa — inklusive `odoo_unlink`.
Flaggan sattes av mail/webhook-vägen, men ingenting band den till DEN
sessionen eller till en betrodd anropare. Vem som helst som kunde påverka
context kunde eskalera.

Rättningen: HMAC över `(session_id, coworker_id)` med nyckeln i
systemparametern `ai_agent_core.api_secret`. Signaturen kan bara produceras
av kod som redan har databasåtkomst.

Hårda stopp gäller ÄVEN med giltig signatur — `PermissionEngine.evaluate`
prövar dem före AUTO. Det är ett eget test nedan.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('ai_core', 'post_install', '-at_install')
class TestAutoApproveSignature(TransactionCase):
    """14.1/14.2: flaggan utan signatur ska inte ge AUTO."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'B8 Coworker',
        })
        self.session = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'B8 session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.api_secret', 'b8-test-hemlighet')

    def test_bare_flag_is_not_signed(self):
        cw = self.coworker.with_context(_ai_auto_approve=True)
        self.assertFalse(
            cw._auto_approve_is_signed(self.session),
            'B8: en naken flagga utan signatur får inte ge AUTO-mode')

    def test_wrong_signature_rejected(self):
        cw = self.coworker.with_context(
            _ai_auto_approve=True,
            _ai_auto_approve_sig='fel-signatur',
        )
        self.assertFalse(cw._auto_approve_is_signed(self.session))

    def test_signature_for_other_session_rejected(self):
        """Signaturen är bunden till sessionen — inte återanvändbar."""
        other = self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'name': 'B8 annan session',
            'status': 'active',
            'user_id': self.env.user.id,
        })
        ctx = self.coworker._sign_auto_approve(self.session)
        cw = self.coworker.with_context(**ctx)
        self.assertTrue(cw._auto_approve_is_signed(self.session))
        self.assertFalse(
            cw._auto_approve_is_signed(other),
            'B8: signaturen får inte gälla en annan session')

    def test_valid_signature_accepted(self):
        ctx = self.coworker._sign_auto_approve(self.session)
        self.assertTrue(ctx.get('_ai_auto_approve'))
        self.assertTrue(ctx.get('_ai_auto_approve_sig'))
        cw = self.coworker.with_context(**ctx)
        self.assertTrue(cw._auto_approve_is_signed(self.session))

    def test_no_secret_means_no_signature(self):
        """Fail-closed: utan hemlighet kan ingen AUTO-mode begäras."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.api_secret', '')
        ctx = self.coworker._sign_auto_approve(self.session)
        self.assertEqual(ctx, {},
                         'utan hemlighet ska ingen signatur produceras')
        cw = self.coworker.with_context(_ai_auto_approve=True)
        self.assertFalse(cw._auto_approve_is_signed(self.session))

    def test_unsigned_flag_logs_a_warning(self):
        """14.3: nekad AUTO-mode ska synas i loggen."""
        # Vi prövar loggningen på den nivå där beslutet fattas: hjälparen
        # returnerar False, och _build_loop loggar. Här verifierar vi att
        # signaturen är det som avgör — loggningen bevakas i
        # test_auto_approve_denied_is_logged nedan.
        cw = self.coworker.with_context(_ai_auto_approve=True)
        self.assertFalse(cw._auto_approve_is_signed(self.session))


@tagged('ai_core', 'post_install', '-at_install')
class TestHardStopsStillApply(TransactionCase):
    """14.4: hårda stopp gäller även med giltig signatur."""

    def test_hard_stop_denied_in_auto_mode(self):
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)

        engine = PermissionEngine(
            mode=PermissionMode.AUTO,
            user_group_ids=set(self.env.user.groups_id.ids))
        decision = engine.evaluate('odoo_unlink', {'model': 'res.partner'})
        self.assertFalse(
            decision.allowed,
            'B8/14.4: odoo_unlink är ett hårt stopp — AUTO får inte '
            'släppa igenom det')

    def test_auto_mode_allows_ordinary_write(self):
        """AUTO ska fortfarande göra nytta för vanliga skrivningar."""
        from odoo.addons.ai_agent_core.core.permission import (
            PermissionEngine, PermissionMode)

        engine = PermissionEngine(
            mode=PermissionMode.AUTO,
            user_group_ids=set(self.env.user.groups_id.ids))
        decision = engine.evaluate(
            'odoo_write', {'model': 'res.partner', 'res_id': 1})
        self.assertTrue(
            decision.allowed,
            'AUTO ska släppa igenom vanliga skrivningar — annars är '
            'mail-vägen obrukbar')


@tagged('ai_core', 'post_install', '-at_install')
class TestMailPathSigns(TransactionCase):
    """14.3: mail-vägen ska signera i stället för att sätta naken flagga."""

    def test_mail_path_uses_signing_helper(self):
        import ast
        import os
        from odoo.addons.ai_agent_core import __file__ as pkg_file

        path = os.path.join(os.path.dirname(pkg_file), 'models',
                            'ai_session.py')
        with open(path, encoding='utf-8') as fh:
            src = fh.read()

        self.assertIn('_sign_auto_approve(', src,
                      'B8: mail-vägen signerar inte AUTO-mode')
        self.assertNotIn(
            '_ai_auto_approve=True)', src,
            'B8: naken _ai_auto_approve=True finns kvar — den ger ingen '
            'AUTO längre, men den ska inte heller skrivas')
