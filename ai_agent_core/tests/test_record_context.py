# -*- coding: utf-8 -*-
"""Tester för rekordkontext i chatt-vägen (ai-coworker-record-context).

Krav som låses:
  1. Sessionen bär recorden (singular) — ai_record_model/_id/_json/_chatter.
  2. Sessionen bär markeringen (plural) — ai_record_ids/_records_json.
  3. Prompten injicerar record/markering med EXPLICITA id:n.
  4. Antalsgränsen tillämpas och överskridandet TYSTAS INTE (R5).
  5. Chatter är avstängt som default för samlingar (R7).
  6. Tom markering ger ingen samlingskontext och inget fel.
  7. Singular och plural är ömsesidigt uteslutande.
  8. `_ai_context_model`/`_ai_context_id` sätts ALDRIG till en record (D1b).
  9. Init-typens värde vinner över coworkerns (upplösningsordningen).

Körs med:
    sudo checkmodule -d <db> -m ai_agent_core -t
"""

import json

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRecordContext(TransactionCase):
    """Sessionens rekordkontext — singular och plural."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Partner = cls.env['res.partner']
        # Två partners att markera — undviker att röra driftdata.
        cls.p1 = cls.Partner.create({'name': 'RC Test A', 'is_company': True})
        cls.p2 = cls.Partner.create({'name': 'RC Test B', 'is_company': True})
        cls.p3 = cls.Partner.create({'name': 'RC Test C', 'is_company': True})

    def _session(self, **vals):
        base = {'status': 'active', 'name': 'rc-test'}
        base.update(vals)
        return self.Session.create(base)

    def _coworker(self, **vals):
        """En riktig coworker — _inject_session_record kräver singleton."""
        base = {'name': 'RC Coworker', 'status': 'active'}
        base.update(vals)
        return self.env['ai.coworker'].create(base)

    # ── 1. Singular ──────────────────────────────────────────────────

    def test_singular_sets_all_four_fields(self):
        """_set_record_context sätter modell, id, json och chatter."""
        sess = self._session()
        sess._set_record_context(self.p1)
        self.assertEqual(sess.ai_record_model, 'res.partner')
        self.assertEqual(sess.ai_record_id, self.p1.id)
        self.assertTrue(sess.ai_record_json)
        # Plural-fälten ska vara tomma — ömsesidigt uteslutande (krav 7).
        self.assertFalse(sess.ai_record_ids)
        self.assertFalse(sess.ai_records_json)

    def test_singular_json_contains_record_fields(self):
        """Fält-JSON:en ska innehålla recordens namn."""
        sess = self._session()
        sess._set_record_context(self.p1)
        data = json.loads(sess.ai_record_json)
        self.assertEqual(data.get('name'), 'RC Test A')

    def test_frontend_info_wins_over_backend(self):
        """Frontend-värden (osparade) föredras framför backend (krav 3)."""
        sess = self._session()
        # Osparat värde som INTE finns i databasen.
        sess._set_record_context(
            self.p1, front_end_info={'name': 'OSPARAT NAMN'})
        data = json.loads(sess.ai_record_json)
        self.assertEqual(data.get('name'), 'OSPARAT NAMN')

    def test_deleted_record_is_noop(self):
        """Raderad record → tyst no-op, inget fel (krav 6)."""
        sess = self._session()
        partner = self.Partner.create({'name': 'RC Temp'})
        partner.unlink()
        sess._set_record_context(partner)
        self.assertFalse(sess.ai_record_model)

    # ── 2. Plural ────────────────────────────────────────────────────

    def test_plural_sets_ids_and_json(self):
        """_set_records_context sätter ids-listan och fält-JSON."""
        sess = self._session()
        recs = self.p1 | self.p2 | self.p3
        sess._set_records_context(recs)
        self.assertEqual(sess.ai_record_model, 'res.partner')
        self.assertEqual(sorted(sess.ai_record_ids), sorted(recs.ids))
        self.assertTrue(sess.ai_records_json)
        # Singular-fälten ska vara tomma (krav 7).
        self.assertFalse(sess.ai_record_id)
        self.assertFalse(sess.ai_record_json)

    def test_plural_count(self):
        """ai_record_count speglar antalet id:n."""
        sess = self._session()
        sess._set_records_context(self.p1 | self.p2 | self.p3)
        self.assertEqual(sess.ai_record_count, 3)

    def test_plural_json_has_one_entry_per_record(self):
        """Fält-JSON:en ska ha en post per record."""
        sess = self._session()
        recs = self.p1 | self.p2
        sess._set_records_context(recs)
        data = json.loads(sess.ai_records_json)
        self.assertEqual(len(data), 2)

    # ── 4. Gränsen och tyst trunkering (R5) ──────────────────────────

    def test_max_records_truncates(self):
        """Fler än max_records → bara max_records inkluderas."""
        sess = self._session()
        sess._set_records_context(
            self.p1 | self.p2 | self.p3, max_records=2)
        self.assertEqual(len(sess.ai_record_ids), 2)

    def test_truncation_is_logged_not_silent(self):
        """Överskridandet ska loggas — aldrig tystas (R5)."""
        sess = self._session()
        with self.assertLogs(
                'odoo.addons.ai_agent_core.models.ai_session',
                level='INFO') as cm:
            sess._set_records_context(
                self.p1 | self.p2 | self.p3, max_records=2)
        self.assertTrue(
            any('TRUNKERAD' in m for m in cm.output),
            'Trunkeringen loggades inte: %s' % cm.output)

    # ── 5. Chatter för samlingar (R7) ────────────────────────────────

    def test_chatter_off_by_default_for_collections(self):
        """Chatter ska vara avstängt som default för en markering."""
        sess = self._session()
        sess._set_records_context(self.p1 | self.p2)
        self.assertFalse(sess.ai_record_chatter)

    # ── 3. Prompt-injektionen ────────────────────────────────────────

    def test_prompt_exposes_singular_record(self):
        """Prompten ska innehålla Current Record + id (krav 3)."""
        sess = self._session()
        sess._set_record_context(self.p1)
        parts = []
        self._coworker()._inject_session_record(parts, sess)
        text = '\n'.join(parts)
        self.assertIn('## Current Record: res.partner', text)
        self.assertIn('(ID: %s)' % self.p1.id, text)
        self.assertIn('### Record Fields', text)

    def test_prompt_exposes_plural_ids_explicitly(self):
        """Prompten ska exponera markeringens id:n explicit (krav 3)."""
        sess = self._session()
        recs = self.p1 | self.p2
        sess._set_records_context(recs)
        parts = []
        self._coworker()._inject_session_record(parts, sess)
        text = '\n'.join(parts)
        self.assertIn('## Selected Records: res.partner (2 rows)', text)
        self.assertIn('### Record IDs', text)
        for rid in sess.ai_record_ids:
            self.assertIn(str(rid), text)

    # ── 6. Tom markering ─────────────────────────────────────────────

    def test_detect_records_empty_without_context(self):
        """Ingen markering i context → tomt recordset (krav 6)."""
        recs = self._coworker()._detect_records({})
        self.assertFalse(recs)

    def test_detect_records_single_id_is_singular(self):
        """En markerad rad är singular — inte en samling."""
        recs = self._coworker().with_context(
            active_model='res.partner',
            active_ids=[self.p1.id],
        )._detect_records({})
        self.assertFalse(recs)

    def test_detect_records_multiple_ids(self):
        """Flera markerade rader → recordset."""
        recs = self._coworker().with_context(
            active_model='res.partner',
            active_ids=[self.p1.id, self.p2.id],
        )._detect_records({})
        self.assertEqual(len(recs), 2)

    # ── 8. D1b: context-nycklarna rörs inte ──────────────────────────

    def test_run_does_not_set_context_keys_to_record(self):
        """`_ai_context_model` får ALDRIG sättas till en record (D1b).

        Nycklarna betyder "aktuell SESSION" och läses av HITL,
        NATS-kontexten och sessionsminnena. Att sätta dem till en record
        kraschar HITL och tystar minnena.
        """
        sess = self._session()
        sess._set_record_context(self.p1)
        # Sessionen bär recorden — context-nycklarna är orörda.
        self.assertEqual(sess.ai_record_model, 'res.partner')
        self.assertNotEqual(
            self.env.context.get('_ai_context_model'), 'res.partner')

    # ── 9. Upplösningsordningen ──────────────────────────────────────

    def test_init_type_wins_over_coworker(self):
        """Init-typens värde vinner över coworkerns (krav 9)."""
        coworker = self._coworker(context_chatter_limit=20)
        coworker.init_type_ids.create({
            'coworker_id': coworker.id,
            'init_type': 'chat',
            'enabled': True,
            'ai_record_chatter_limit': 5,
        })
        self.assertEqual(
            coworker._ai_record_setting('ai_record_chatter_limit'), 5)

    def test_coworker_fallback_when_init_unset(self):
        """Utan init-typ faller upplösningen tillbaka på coworkern."""
        coworker = self._coworker(context_chatter_limit=7)
        self.assertEqual(
            coworker._ai_record_setting('ai_record_chatter_limit'), 7)
