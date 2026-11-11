# -*- coding: utf-8 -*-
"""Hela tidskedjan, bevisad i kärnan (okf-temporal-recall task 6.1).

VARFÖR ETT TEST MED EN EGEN MODELL I STÄLLET FÖR MAIL/KALENDER: bryggorna
ligger i andra repon. Om beviset krävde dem skulle ändringen i kärnan vara
overifierad tills de levererats — och en sökväg som "borde fungera" är
exakt vad `okf-recall-path` kostade tre rundor att städa bort.

Testmodellen ärver mixinen, implementerar `_okf_source_date()` och
registrerar en tidsdefault. Då prövas hela kedjan:

    indexering → source_date på konceptet → sökning med tidsvikt

utan att kärnan nämner en enda domänmodell.
"""

import json
from datetime import datetime, timedelta

from odoo.tests import common, tagged
from unittest.mock import patch


class _TemporalTestModel(common.TransactionCase):
    """Bas: en indexerbar modell med ett känt källdatum."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mixin = cls.env['ai.okf.mixin']
        cls.Concept = cls.env['ai.okf.concept']
        cls.company = cls.env.company

    def setUp(self):
        super().setUp()
        self._embed_patch = patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            return_value=None)
        self._embed_patch.start()

    def tearDown(self):
        self._embed_patch.stop()
        super().tearDown()

    def _concept(self, key, summary, source_date=None, role=None,
                 artifact_type='knowledge'):
        """Skriv ett koncept direkt (utan indexeringskedjan)."""
        vals = {
            'artifact_type': artifact_type,
            'concept_key': key,
            'summary': summary,
            'title': summary[:60],
            # `source_ref` måste peka på en modell som FINNS: access-
            # resolvern klassar en okänd modell som CLOSED (fail-closed),
            # och då filtreras konceptet bort ur varje sökning. `res.partner`
            # är alltid installerad och bär ingen känslig data här.
            'source_ref': 'res.partner,%s' % (abs(hash(key)) % 100000),
            'owner_company_id': self.company.id,
            'source_date': source_date,
            'temporal_role': role,
        }
        return self.Concept._okf_upsert(**vals)


@tagged('post_install', '-at_install', 'okf', 'temporal')
class TestTemporalContract(_TemporalTestModel):
    """Kontraktet: bryggan pekar ut tiden, kärnan läser den."""

    def test_default_source_date_is_empty(self):
        """Utan implementation gissar kärnan INGET.

        En gissning hade gett `create_date` (indexeringstid) för varje
        modell som glömde metoden — och då vore hela axeln meningslös.
        """
        self.assertEqual(self.Mixin._okf_source_date(), (None, None))

    def test_concept_carries_source_date(self):
        when = datetime(2026, 11, 5, 9, 0)
        c = self._concept('temporal.a', 'Ett mail om Skanska', when,
                          'received')
        self.assertEqual(c.source_date, when)
        self.assertEqual(c.temporal_role, 'received')

    def test_upsert_updates_source_date_on_reindex(self):
        """Källan speglas — ett omindexerat koncept får den nya tiden."""
        old = datetime(2026, 10, 1, 8, 0)
        new = datetime(2026, 11, 9, 8, 0)
        c = self._concept('temporal.b', 'Uppdaterad', old, 'received')
        c2 = self._concept('temporal.b', 'Uppdaterad', new, 'received')
        self.assertEqual(c.id, c2.id, 'en rad per nyckel, ingen version')
        self.assertEqual(c2.source_date, new)

    def test_source_date_survives_null(self):
        """Koncept utan källdatum är giltiga — de rankas bara utan tid."""
        c = self._concept('temporal.c', 'Inget datum')
        self.assertFalse(c.source_date)
        self.assertFalse(c.temporal_role)


@tagged('post_install', '-at_install', 'okf', 'temporal')
class TestTemporalDefaults(_TemporalTestModel):
    """Registreringen av tidsdefault (task 3.3, 10.1)."""

    def test_register_temporal_default(self):
        self.Mixin._okf_register_temporal_default(
            'test_mail', days=7, direction='backward')
        defaults = self.Mixin._okf_temporal_defaults()
        self.assertIn('test_mail', defaults)
        self.assertEqual(defaults['test_mail']['days'], 7)
        self.assertEqual(defaults['test_mail']['direction'], 'backward')

    def test_register_is_idempotent(self):
        self.Mixin._okf_register_temporal_default('test_x', 7, 'backward')
        self.Mixin._okf_register_temporal_default('test_x', 14, 'forward')
        defaults = self.Mixin._okf_temporal_defaults()
        self.assertEqual(defaults['test_x']['days'], 14)
        self.assertEqual(defaults['test_x']['direction'], 'forward')

    def test_register_related(self):
        self.Mixin._okf_register_related(
            'test_task', 'project.task', date_field='date_deadline',
            label_field='name', partner_field='partner_id')
        registry = self.Mixin._okf_related_models()
        self.assertIn('project.task', registry)
        self.assertEqual(registry['project.task']['kind'], 'test_task')
        self.assertEqual(registry['project.task']['date_field'],
                         'date_deadline')

    def test_bad_days_is_rejected_not_crashing(self):
        ok = self.Mixin._okf_register_temporal_default(
            'test_bad', days='inte-ett-tal')
        self.assertFalse(ok)
        self.assertNotIn('test_bad', self.Mixin._okf_temporal_defaults())


@tagged('post_install', '-at_install', 'okf', 'temporal')
class TestTemporalSearch(_TemporalTestModel):
    """Tidsvikten i sökvägen (task 4.2–4.4)."""

    def test_time_window_compares_source_date_not_write_date(self):
        """Task 4.1 — axelbytet.

        Ett koncept vars källdatum är gammalt men som skrevs i dag ska
        INTE komma med i ett fönster som bara rymmer de senaste dagarna.
        Med `write_date` hade det kommit med — det är hela buggen.
        """
        old = datetime.now() - timedelta(days=400)
        c = self._concept('temporal.old', 'Gammalt mail om Skanska',
                          old, 'received')
        recent = self.Concept._okf_search(
            'Skanska', scope='company', owner_id=self.company.id,
            limit=10, semantic=False,
            time_window=datetime.now() - timedelta(days=7))
        self.assertNotIn(c.id, recent.ids)

    def test_hits_outside_the_window_are_still_returned(self):
        """Tiden styr ordningen, ALDRIG urvalet (task 4.2)."""
        old = datetime.now() - timedelta(days=400)
        new = datetime.now() - timedelta(days=1)
        c_old = self._concept('temporal.rank.old',
                              'Skanska avtal gammalt', old, 'received')
        c_new = self._concept('temporal.rank.new',
                              'Skanska avtal nytt', new, 'received')
        hits = self.Concept._okf_search(
            'Skanska avtal', scope='company', owner_id=self.company.id,
            limit=10, semantic=False,
            temporal_defaults={'knowledge': {'days': 7,
                                             'direction': 'backward'}})
        self.assertIn(c_old.id, hits.ids, 'gammal träff får inte försvinna')
        self.assertIn(c_new.id, hits.ids)
        self.assertLess(
            hits.ids.index(c_new.id), hits.ids.index(c_old.id),
            'närtid ska rankas före')

    def test_temporal_disabled_keeps_the_old_behaviour(self):
        """Avstängd tidsvikt → ingen tidsvikt (task 5.2)."""
        old = datetime.now() - timedelta(days=400)
        c = self._concept('temporal.off', 'Skanska gammalt', old, 'received')
        hits = self.Concept._okf_search(
            'Skanska gammalt', scope='company', owner_id=self.company.id,
            limit=10, semantic=False, temporal=False)
        self.assertIn(c.id, hits.ids)

    def test_diagnostics_report_the_window(self):
        """Task 4.6 — fönstret ska gå att förklara."""
        self.Concept._okf_search(
            'Skanska i förra veckan', scope='company',
            owner_id=self.company.id, limit=5, semantic=False,
            temporal_defaults={'knowledge': {'days': 7,
                                             'direction': 'backward'}})
        diag = self.Concept._okf_temporal_diag()
        self.assertEqual(diag['source'], 'explicit')
        self.assertIsNotNone(diag['from'])
        self.assertIsNotNone(diag['to'])

    def test_diagnostics_name_an_uninterpreted_expression(self):
        self.Concept._okf_search(
            'Skanska i förfjol', scope='company',
            owner_id=self.company.id, limit=5, semantic=False,
            temporal=False)
        diag = self.Concept._okf_temporal_diag()
        self.assertEqual(diag['unmatched'], 'i forfjol')


@tagged('post_install', '-at_install', 'okf', 'temporal')
class TestTypedLinks(_TemporalTestModel):
    """Typade länkar och bakåtuppslag (task 9)."""

    def test_legacy_string_link_is_read_as_explicit(self):
        """Bakåtkompatibilitet utan datamigrering (task 9.1)."""
        normalized = self.Concept._okf_normalize_links(
            ['project.project,7'])
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]['ref'], 'project.project,7')
        self.assertEqual(normalized[0]['kind'], 'reference')
        self.assertEqual(normalized[0]['basis'], 'explicit')
        self.assertIsNone(normalized[0]['date'])

    def test_typed_link_is_preserved(self):
        normalized = self.Concept._okf_normalize_links([{
            'ref': 'project.task,42', 'kind': 'project_task',
            'date': '2026-11-12T00:00:00', 'basis': 'temporal'}])
        self.assertEqual(normalized[0]['kind'], 'project_task')
        self.assertEqual(normalized[0]['basis'], 'temporal')
        self.assertEqual(normalized[0]['date'], '2026-11-12T00:00:00')

    def test_backwards_lookup_finds_the_concept(self):
        """Task 9.5 — 'vilka koncept pekar på X'."""
        c = self._concept('temporal.links', 'Länkat koncept')
        c.write({'okf_links': [{
            'ref': 'project.project,7', 'kind': 'project_project',
            'date': None, 'basis': 'explicit'}]})
        found = self.Concept._okf_concepts_pointing_to(
            'project.project', 7, limit=10)
        self.assertIn(c.id, found.ids)

    def test_backwards_lookup_matches_legacy_strings(self):
        """Gamla strängposter ska också hittas av bakåtfrågan."""
        c = self._concept('temporal.links.legacy', 'Legacy-länk')
        c.write({'okf_links': ['project.project,9']})
        found = self.Concept._okf_concepts_pointing_to(
            'project.project', 9, limit=10)
        self.assertIn(c.id, found.ids)

    def test_backwards_lookup_ignores_other_targets(self):
        c = self._concept('temporal.links.other', 'Annan länk')
        c.write({'okf_links': [{'ref': 'project.project,11'}]})
        found = self.Concept._okf_concepts_pointing_to(
            'project.project', 12, limit=10)
        self.assertNotIn(c.id, found.ids)
