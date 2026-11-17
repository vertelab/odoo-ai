# -*- coding: utf-8 -*-
"""Tester för kontextbryggan — DM/chatt med record-kontext (systray-vägen).

Porterad från ai_agent_context (2026-11-16).

VAD SOM LÅSES
=============
Syftet är att användaren ska kunna ställa frågor OM den post hen har framför
sig — t.ex. en helpdesk-ticket — i en DM med en AI-medarbetare.

  1. /ai/context/launch skapar en kanal och sätter postens kontext på den.
  2. /ai/context/set uppdaterar kontexten utan att köra en tur.
  3. Kanalens kontext läses av _get_ai_context_record() — den väg som
     ai.coworker._detect_record() använder (källa 4a).
  4. Markering (list-vy) ger plural-kontext, form-vy ger singular.
  5. Kanalkontexten skrivs INTE över av enbart modell-kontext.
  6. Obehörig/okänd coworker ger fel, inte krasch.
  7. Kontexten överlever att turen går (den ligger på kanalen).

KÖRNING
=======
    sudo checkmodule -d <db> -m ai_agent_core -t
"""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestContextBridge(TransactionCase):
    """Kontextbryggan — kanal, session och record-kontext."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Channel = cls.env['discuss.channel']
        cls.Coworker = cls.env['ai.coworker']
        cls.Session = cls.env['ai.coworker.session']
        cls.Partner = cls.env['res.partner']

        cls.p1 = cls.Partner.create({'name': 'CB Test A', 'is_company': True})
        cls.p2 = cls.Partner.create({'name': 'CB Test B', 'is_company': True})

        cls.coworker = cls.Coworker.create({
            'name': 'CB Test Coworker',
            'description': 'Test-coworker för kontextbryggan',
            'active': True,
        })

    # ── Hjälpare ──────────────────────────────────────────────────────

    def _channel(self):
        return self.Channel.create({'name': 'CB Test Channel'})

    def _set(self, channel, **kw):
        """Sätt kontexten — samma två steg som bryggan gör."""
        extra = {
            'context_model': kw.get('model'),
            'context_res_id': kw.get('res_id') or False,
            'context_res_ids': kw.get('res_ids') or False,
            'view_type': kw.get('view_type'),
        }
        channel._capture_ai_context(extra_context=extra)
        if kw.get('record_data') and kw.get('model'):
            channel._store_ai_context_snapshot(
                kw.get('model'), kw.get('res_id'), kw.get('res_ids'),
                kw.get('record_data'))

    def _bridge(self):
        """Controllern som vanlig Python-klass — vi testar logiken, inte HTTP."""
        from odoo.addons.ai_agent_core.controllers.context_bridge import (
            AIContextBridge,
        )
        return AIContextBridge()

    # ── 1. Singular kontext (form-vy) ─────────────────────────────────

    def test_01_set_singular_context(self):
        """Form-vy: kanalen bär EN record."""
        channel = self._channel()
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')

        self.assertEqual(channel.ai_context_model, 'res.partner')
        self.assertEqual(channel.ai_context_record_id, self.p1.id)
        self.assertFalse(channel.ai_context_record_ids)

    def test_02_get_ai_context_record_returns_record(self):
        """_get_ai_context_record() — vägen _detect_record() läser."""
        channel = self._channel()
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')

        recs, view_type = channel._get_ai_context_record()
        self.assertEqual(len(recs), 1,
                         'kontexten ska ge exakt en record')
        self.assertEqual(recs.id, self.p1.id)
        self.assertEqual(view_type, 'form')

    # ── 2. Plural kontext (list-vy-markering) ─────────────────────────

    def test_03_set_plural_context(self):
        """List-vy: kanalen bär en markering."""
        channel = self._channel()
        ids = [self.p1.id, self.p2.id]
        self._set(channel, model='res.partner', res_ids=ids,
                  view_type='list')

        self.assertEqual(channel.ai_context_model, 'res.partner')
        self.assertFalse(channel.ai_context_record_id)
        self.assertEqual(list(channel.ai_context_record_ids or []), ids)

    def test_04_get_ai_context_record_plural(self):
        """Markeringen kommer tillbaka som ett recordset."""
        channel = self._channel()
        ids = [self.p1.id, self.p2.id]
        self._set(channel, model='res.partner', res_ids=ids,
                  view_type='list')

        recs, view_type = channel._get_ai_context_record()
        self.assertEqual(len(recs), 2)
        self.assertEqual(set(recs.ids), set(ids))
        self.assertEqual(view_type, 'list')

    # ── 3. Singular och plural är ömsesidigt uteslutande ──────────────

    def test_05_singular_overrides_plural(self):
        """Att sätta en enstaka record rensar markeringen."""
        channel = self._channel()
        self._set(channel, model='res.partner', res_ids=[self.p1.id, self.p2.id],
                  view_type='list')
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')

        self.assertEqual(channel.ai_context_record_id, self.p1.id)
        self.assertFalse(channel.ai_context_record_ids)

    def test_06_plural_overrides_singular(self):
        """Att sätta en markering rensar den enstaka recorden."""
        channel = self._channel()
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')
        self._set(channel, model='res.partner',
                  res_ids=[self.p1.id, self.p2.id], view_type='list')

        self.assertFalse(channel.ai_context_record_id)
        self.assertEqual(len(channel.ai_context_record_ids or []), 2)

    # ── 4. Modell-kontext skriver inte över en specifik record ────────

    def test_07_model_only_does_not_clobber_record(self):
        """Enbart modell får inte radera en existerande record-kontext.

        Regeln finns i _capture_ai_context: annars tappar en DM sin record
        så fort användaren klickar i en meny.
        """
        channel = self._channel()
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')
        self._set(channel, model='res.partner', view_type=False)

        self.assertEqual(channel.ai_context_record_id, self.p1.id,
                         'record-kontexten ska överleva modell-only')

    # ── 5. Session-snapshot ───────────────────────────────────────────

    def test_08_snapshot_lands_on_session(self):
        """Frontendens fältvärden hamnar på sessionen — inte bara kanalen."""
        channel = self._channel()
        channel.write({'ai_coworker_ids': [(4, self.coworker.id)]})
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'name': 'CB snapshot-session',
        })

        data = {'name': 'Osparad ändring', 'custom': 42}
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form', record_data=data)

        self.assertEqual(session.ai_record_model, 'res.partner')
        self.assertEqual(session.ai_record_id, self.p1.id)
        stored = json.loads(session.ai_record_json or '{}')
        self.assertEqual(stored.get('name'), 'Osparad ändring')

    def test_09_snapshot_plural(self):
        """Markeringens fältvärden hamnar som plural på sessionen."""
        channel = self._channel()
        channel.write({'ai_coworker_ids': [(4, self.coworker.id)]})
        session = self.Session.create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'name': 'CB plural-session',
        })
        ids = [self.p1.id, self.p2.id]
        self._set(channel, model='res.partner', res_ids=ids,
                  view_type='list', record_data={'rows': ids})

        self.assertFalse(session.ai_record_id)
        self.assertEqual(list(session.ai_record_ids or []), ids)

    # ── 6. Ingen session = ingen krasch ───────────────────────────────

    def test_10_no_session_is_silent(self):
        """Utan kopplad session sätts kanalkontexten ändå — tyst no-op."""
        channel = self._channel()
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form', record_data={'name': 'x'})
        self.assertEqual(channel.ai_context_record_id, self.p1.id)

    # ── 7. Kontexten överlever en tur ─────────────────────────────────

    def test_11_context_survives_message_post(self):
        """Att posta ett meddelande får inte radera kontexten."""
        channel = self._channel()
        channel.write({'ai_coworker_ids': [(4, self.coworker.id)]})
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')

        channel.message_post(body='Hej, vad är detta?')

        self.assertEqual(channel.ai_context_record_id, self.p1.id)

    # ── 8. _detect_record hittar kanalkontexten ──────────────────────

    def test_12_detect_record_reads_channel_context(self):
        """ai.coworker._detect_record() ska hitta recorden via kanalen.

        Detta är kärnan i systray-flödet: användaren frågar i DM:en och
        coworkern måste veta VILKEN post frågan gäller.
        """
        channel = self._channel()
        channel.write({'ai_coworker_ids': [(4, self.coworker.id)]})
        self._set(channel, model='res.partner', res_id=self.p1.id,
                  view_type='form')

        found = self.coworker._detect_record({'channel': channel})
        self.assertTrue(found, '_detect_record ska hitta recorden')
        self.assertEqual(found._name, 'res.partner')
        self.assertEqual(found.id, self.p1.id)

    def test_13_detect_record_plural_via_channel(self):
        """Markeringen hittas av _detect_records()."""
        channel = self._channel()
        channel.write({'ai_coworker_ids': [(4, self.coworker.id)]})
        ids = [self.p1.id, self.p2.id]
        self._set(channel, model='res.partner', res_ids=ids, view_type='list')

        found = self.coworker._detect_records({'channel': channel})
        self.assertEqual(set(found.ids), set(ids))


@tagged('post_install', '-at_install')
class TestContextBridgeChannel(TransactionCase):
    """Kanalåteranvändning — en DM per coworker, inte en per klick."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']

        cls.coworker = cls.Coworker.create({
            'name': 'CB Reuse Coworker',
            'description': 'Test',
            'active': True,
        })

    def _bridge(self):
        from odoo.addons.ai_agent_core.controllers.context_bridge import (
            AIContextBridge,
        )
        return AIContextBridge()

    def test_20_reuses_existing_channel(self):
        """Två anrop ska ge SAMMA kanal — inte en ny DM per klick."""
        bridge = self._bridge()
        first = bridge._get_or_create_channel(self.coworker)
        second = bridge._get_or_create_channel(self.coworker)
        self.assertEqual(first.id, second.id,
                         'kanalen ska återanvändas mellan klick')

    def test_21_channel_has_coworker(self):
        """Den skapade kanalen måste ha coworkern kopplad.

        Annars hittar _get_session_for_channel ingen session och
        @mention-routing fungerar inte.
        """
        bridge = self._bridge()
        channel = bridge._get_or_create_channel(self.coworker)
        self.assertIn(self.coworker, channel.ai_coworker_ids)
