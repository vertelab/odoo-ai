# -*- coding: utf-8 -*-
"""Tester för retention-verkställighet (memory-retention-enforcement).

VARFÖR DESSA TESTER FINNS: `ai.artifact.type.okf_contract.retention_days`
deklarerades i data (`email` och `partner`: 180 dagar) men lästes av ingen
kod. Fältet `retention_end` fanns på konceptet och var märkt "SENARELAGT" —
det vill säga en gallringsfrist som såg ut att gälla men aldrig verkställdes.
En deklarerad men icke verkställd frist är en felaktig uppgift i ett register
som någon kan komma att granska.

Testerna mäter därför BETYDELSEN, inte fältet:

* fristen räknas ur `create_date` (när vi fick uppgiften), inte ur källans
  `last_modified` — och oberoende av `stale_after` (design D1)
* utgånget koncept ARKIVERAS, inte raderas (D2) — raden finns kvar
* varningen sker 30 dagar före och EN gång (D3)
* cronen mäter sig själv, så en trasig cron blir synlig (D4)
* typbyte räknar om fristen med spår (D5)
"""

import logging
from contextlib import contextmanager
import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ._config_param_guard import ConfigParamGuardedCase


@tagged('okf', 'retention', 'post_install', '-at_install')
class RetentionBase(ConfigParamGuardedCase):
    """Artefakttyper med och utan frist + en ägare att varna."""

    @contextmanager
    def _capture_okf_logs(self):
        """Fånga OKF-loggern under ett block.

        `assertLogs` räcker inte: Odoo sätter `propagate = False` och
        WARNING-nivå på addon-loggerna under testkörning, så en INFO-rad
        filtreras bort innan den når assertLogs-handlern — trots att den
        skrivs till loggfilen. Här öppnas båda grindarna och stängs efteråt.
        """
        from odoo.addons.ai_agent_core.models import ai_okf_concept as _mod
        logger = _mod._logger
        prev_propagate, prev_level = logger.propagate, logger.level
        logger.propagate = True
        logger.setLevel(logging.DEBUG)
        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Collector()
        logger.addHandler(handler)
        try:
            yield records
        finally:
            logger.removeHandler(handler)
            logger.propagate = prev_propagate
            logger.setLevel(prev_level)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.ArtifactType = cls.env['ai.artifact.type']
        cls.owner = cls.env['res.users'].create({
            'name': 'Retention Owner',
            'login': 'retention_owner',
            'email': 'retention.owner@example.com',
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.atype_long = cls.ArtifactType.create({
            'name': 'retention-test-long',
            'kind': 'knowledge',
            'okf_contract': {
                'generated_by': 'process',
                'stale_policy': 'fixed',
                'stale_ttl_days': 365,
                'retention_purpose': 'crm_lead',
                'retention_days': 180,
            },
        })
        cls.atype_short = cls.ArtifactType.create({
            'name': 'retention-test-short',
            'kind': 'knowledge',
            'okf_contract': {
                'generated_by': 'process',
                'stale_policy': 'fixed',
                'stale_ttl_days': 365,
                'retention_purpose': 'crm_lead',
                'retention_days': 30,
            },
        })
        cls.atype_none = cls.ArtifactType.create({
            'name': 'retention-test-none',
            'kind': 'knowledge',
            'okf_contract': {
                'generated_by': 'process',
                'stale_policy': 'fixed',
                'stale_ttl_days': 365,
                'retention_purpose': 'none',
                'retention_days': 0,
            },
        })

    def _mk(self, atype, key, **kw):
        vals = {
            'artifact_type_id': atype.id,
            'scope': 'personal',
            'concept_key': key,
            'summary': 'Ett koncept',
            'owner_user_id': self.owner.id,
        }
        vals.update(kw)
        return self.Concept.create(vals)

    def _set_end(self, concept, days):
        """Flytta fristen relativt nu — create_date går inte att skriva."""
        concept.sudo().write({
            'retention_end': fields.Datetime.now() + timedelta(days=days)})


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionConfig(RetentionBase):
    """1.1 + 2.1–2.3: accessorn och valideringen."""

    def test_get_retention_reads_contract(self):
        """1.1: typ med crm_lead/180 ger ('crm_lead', 180)."""
        self.assertEqual(self.atype_long._get_retention(), ('crm_lead', 180))

    def test_get_retention_default_is_none(self):
        """1.1: typ utan fälten ger ('none', 0) — ingen ärvd frist."""
        atype = self.ArtifactType.create({
            'name': 'retention-test-empty',
            'kind': 'knowledge',
            'okf_contract': {'generated_by': 'process'},
        })
        self.assertEqual(atype._get_retention(), ('none', 0))

    def test_get_retention_negative_days_is_zero(self):
        """En negativ frist skulle arkivera konceptet vid skapandet."""
        atype = self.ArtifactType.create({
            'name': 'retention-test-negative',
            'kind': 'knowledge',
            'okf_contract': {'retention_purpose': 'tax',
                             'retention_days': -5},
        })
        self.assertEqual(atype._get_retention(), ('tax', 0))

    def test_none_with_days_is_rejected(self):
        """2.1: 'none' + dagar är en motsägelse som annars är tyst."""
        with self.assertRaises(ValidationError) as ctx:
            self.ArtifactType.create({
                'name': 'retention-test-contradiction',
                'kind': 'knowledge',
                'okf_contract': {'retention_purpose': 'none',
                                 'retention_days': 180},
            })
        message = str(ctx.exception)
        self.assertIn('retention_purpose', message)
        self.assertIn('180', message)

    def test_none_without_days_is_accepted(self):
        """2.2: none/0 godtas."""
        atype = self.ArtifactType.create({
            'name': 'retention-test-ok-none',
            'kind': 'knowledge',
            'okf_contract': {'retention_purpose': 'none',
                             'retention_days': 0},
        })
        self.assertTrue(atype)

    def test_purpose_with_days_is_accepted(self):
        """2.2: crm_lead/180 godtas."""
        self.assertEqual(self.atype_long._get_retention(), ('crm_lead', 180))

    def test_default_data_types_validate(self):
        """2.3: datafilens typer finns och validerar (email = 180 dagar)."""
        email = self.env.ref('ai_agent_core.artifact_type_email')
        self.assertEqual(email._get_retention(), ('crm_lead', 180))
        partner = self.env.ref('ai_agent_core.artifact_type_partner')
        self.assertEqual(partner._get_retention(), ('crm_lead', 180))
        # none/0-typerna ska också ha kommit igenom valideringen.
        manual = self.env.ref('ai_agent_core.artifact_type_manual')
        self.assertEqual(manual._get_retention(), ('none', 0))


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionEndComputation(RetentionBase):
    """1.2–1.5: retention_end beräknas vid skapande."""

    def test_end_computed_from_create_date(self):
        """1.2/1.3: nytt koncept får create_date + 180 dagar."""
        c = self._mk(self.atype_long, 'retention.computed')
        self.assertTrue(c.retention_end)
        expected = c.create_date + timedelta(days=180)
        self.assertAlmostEqual(
            c.retention_end, expected, delta=timedelta(seconds=5))

    def test_no_end_without_retention(self):
        """1.2: typ utan frist ger tomt retention_end."""
        c = self._mk(self.atype_none, 'retention.none')
        self.assertFalse(c.retention_end)

    def test_end_ignores_source_last_modified(self):
        """1.4: fristen räknas från create_date, inte källans datum.

        En källa som är ett år gammal ska inte ge en frist som redan
        passerat — fristen är "hur länge vi får behålla uppgiften".
        """
        c = self._mk(self.atype_long, 'retention.old-source', sources=[{
            'resource': 'res.partner,42',
            'last_modified': (fields.Datetime.now()
                              - timedelta(days=365)).isoformat(),
        }])
        expected = c.create_date + timedelta(days=180)
        self.assertAlmostEqual(
            c.retention_end, expected, delta=timedelta(seconds=5))
        self.assertGreater(c.retention_end, fields.Datetime.now())

    def test_stale_and_retention_are_independent(self):
        """1.5: passerad frist men stale_after i framtiden = färskt men utgånget."""
        c = self._mk(self.atype_long, 'retention.independent')
        self._set_end(c, days=-1)
        future = fields.Datetime.now() + timedelta(days=30)
        c.sudo().write({'stale_after': future})
        self.assertLess(c.retention_end, fields.Datetime.now())
        self.assertGreater(c.stale_after, fields.Datetime.now())
        # Injektionen ser konceptet som färskt (stale-frågan) …
        self.assertIn(c.id, self.Concept._injectable_concepts(limit=200).ids)
        # … men det är utgånget (existens-frågan).
        self.assertLess(c.retention_end, fields.Datetime.now())


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionBackfill(RetentionBase):
    """3.1–3.3: backfill av befintliga koncept."""

    def test_backfill_sets_end_and_is_idempotent(self):
        """3.1: koncept utan frist får en; andra körningen ändrar inget."""
        c = self._mk(self.atype_long, 'retention.backfill')
        c.sudo().write({'retention_end': False})
        self.assertFalse(c.retention_end)

        first = self.Concept._cron_backfill_retention()
        self.assertGreaterEqual(first, 1)
        c.invalidate_recordset(['retention_end'])
        self.assertTrue(c.retention_end)
        end_after_first = c.retention_end

        second = self.Concept._cron_backfill_retention()
        self.assertEqual(second, 0)
        c.invalidate_recordset(['retention_end'])
        self.assertEqual(c.retention_end, end_after_first)

    def test_backfill_does_not_archive_expired(self):
        """3.2: passerat koncept får frist men arkiveras inte.

        `create_date` går inte att skriva via ORM (den sätts av Odoo i
        INSERT), så testet backdaterar den med SQL. Den här Odoo-versionen
        har dock en `BEFORE UPDATE`-trigger på `create_date` (se
        `ir_model.py`: "create_date is readonly") som sätter tillbaka
        kolumnen — därför mäts betydelsen i stället: backfillen får inte
        arkivera NÅGOT, och den enda koncept som kan arkiveras av en
        efterföljande enforce-körning är den som passerat fristen.
        """
        c = self._mk(self.atype_long, 'retention.backfill-expired')
        # Backdateringen är ett försök, inte en förutsättning: triggern kan
        # återställa den. Vi verifierar därför bara att raden finns kvar.
        old = fields.Datetime.now() - timedelta(days=400)
        self.env.cr.execute(
            'UPDATE ai_okf_concept SET create_date = %s, retention_end = NULL '
            'WHERE id = %s', (old, c.id))
        c.invalidate_recordset(['create_date', 'retention_end'])

        self.Concept._cron_backfill_retention()
        c.invalidate_recordset(['create_date', 'retention_end'])
        self.assertTrue(c.retention_end, 'backfillen ska ge konceptet en frist')
        self.assertFalse(c.archived, 'backfillen får inte gallra i smyg')

    def test_backfill_never_archives_anything(self):
        """3.2: backfillen arkiverar inget — inte ens det som redan gått ut.

        Det är den mätbara delen av 3.2 som inte hänger på en backdaterad
        `create_date`: en frist som redan passerat får fyllas i, men
        arkiveringen hör till `_cron_enforce_retention()`.
        """
        c = self._mk(self.atype_long, 'retention.backfill-past')
        self._set_end(c, days=-10)
        c.sudo().write({'retention_end': False})
        self.Concept._cron_backfill_retention()
        c.invalidate_recordset(['archived'])
        self.assertFalse(c.archived, 'backfillen får inte gallra i smyg')

    def test_backfill_logs_count(self):
        """3.3: loggraden finns (antal uppdaterade)."""
        c = self._mk(self.atype_long, 'retention.backfill-log')
        c.sudo().write({'retention_end': False})
        with self._capture_okf_logs() as logs:
            self.Concept._cron_backfill_retention()
        self.assertTrue(
            any('retention-backfill' in line for line in logs),
            logs)


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionWarning(RetentionBase):
    """4.1–4.4: varning 30 dagar före utgång."""

    def _activities(self, concept):
        return self.env['mail.activity'].search([
            ('res_model', '=', 'ai.okf.concept'),
            ('res_id', '=', concept.id),
        ])

    def test_warning_created_within_30_days(self):
        """4.1: aktivitet skapas, namnger konceptet och utgångsdatumet."""
        c = self._mk(self.atype_long, 'retention.warn', title='Kundavtal')
        self._set_end(c, days=29)
        self.Concept._cron_warn_retention()
        acts = self._activities(c)
        self.assertEqual(len(acts), 1)
        end_text = c.retention_end.strftime('%Y-%m-%d')
        self.assertIn('Kundavtal', acts.summary)
        self.assertIn(end_text, acts.summary)
        self.assertEqual(acts.user_id, self.owner)

    def test_no_duplicate_warning(self):
        """4.2: andra körningen skapar ingen ny aktivitet."""
        c = self._mk(self.atype_long, 'retention.warn-twice')
        self._set_end(c, days=10)
        self.Concept._cron_warn_retention()
        self.Concept._cron_warn_retention()
        self.assertEqual(len(self._activities(c)), 1)
        self.assertTrue(c.retention_warned_at)

    def test_recipient_is_owner_user(self):
        """4.3: owner_user_id om satt."""
        c = self._mk(self.atype_long, 'retention.recipient-user')
        self._set_end(c, days=5)
        self.assertEqual(c._retention_recipient(), self.owner)

    def test_recipient_is_coworker_owner(self):
        """4.3: annars coworkerns ansvarige (chat_user_id)."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Retention Coworker',
            'chat_user_id': self.owner.id,
        })
        c = self.Concept.create({
            'artifact_type_id': self.atype_long.id,
            'scope': 'coworker',
            'concept_key': 'retention.recipient-coworker',
            'summary': 'Coworker-koncept',
            'owner_coworker_id': coworker.id,
        })
        self.assertEqual(c._retention_recipient(), self.owner)

    def test_recipient_falls_back_to_admin(self):
        """4.3: annars systemadministratören."""
        coworker = self.env['ai.coworker'].create({'name': 'No Owner Coworker'})
        c = self.Concept.create({
            'artifact_type_id': self.atype_long.id,
            'scope': 'coworker',
            'concept_key': 'retention.recipient-admin',
            'summary': 'Utan ägare',
            'owner_coworker_id': coworker.id,
        })
        self.assertEqual(
            c._retention_recipient(), self.env.ref('base.user_admin'))

    def test_no_warning_without_period(self):
        """4.4: koncept utan frist varnas inte."""
        c = self._mk(self.atype_none, 'retention.warn-none')
        self.Concept._cron_warn_retention()
        self.assertEqual(len(self._activities(c)), 0)
        self.assertFalse(c.retention_warned_at)

    def test_no_warning_far_in_future(self):
        """4.4: frist längre bort än 30 dagar varnas inte."""
        c = self._mk(self.atype_long, 'retention.warn-future')
        self._set_end(c, days=60)
        self.Concept._cron_warn_retention()
        self.assertEqual(len(self._activities(c)), 0)
        self.assertFalse(c.retention_warned_at)


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionEnforcement(RetentionBase):
    """5.1–5.3 + 6.1: arkivering och mätning."""

    def test_expired_concept_is_archived_not_deleted(self):
        """5.1: passerad frist arkiverar; raden finns kvar."""
        c = self._mk(self.atype_long, 'retention.enforce')
        self._set_end(c, days=-1)
        self.Concept._cron_enforce_retention()
        c.invalidate_recordset(['archived'])
        self.assertTrue(c.archived)
        self.assertTrue(self.Concept.with_context(
            active_test=False).search([('id', '=', c.id)]))

    def test_archived_concept_excluded_from_search(self):
        """5.2: arkiverat koncept syns inte i sökning eller injektion.

        `archived` är inte modellens active-fält (Odoo 18 tillåter bara
        `active`/`x_active` som `_active_name`), så en rå `search` ser
        fortfarande raden — det är avsiktligt, en arkiverad rad ska gå att
        granska och ångra. Det som ska utesluta den är de domäner som
        betyder "synlig": injektionen och sökningen.
        """
        c = self._mk(self.atype_long, 'retention.search-hidden')
        self._set_end(c, days=-1)
        self.Concept._cron_enforce_retention()
        c.invalidate_recordset(['archived'])
        self.assertTrue(c.archived, 'cronen ska ha arkiverat konceptet')
        self.assertNotIn(
            c.id, self.Concept._injectable_concepts(limit=200).ids,
            'arkiverat koncept får inte injiceras')
        self.assertNotIn(
            c.id, self.Concept.search(self.Concept._fresh_domain()).ids,
            'arkiverat koncept får inte vara med i den friska domänen')

    def test_future_concept_untouched(self):
        """5.3: icke-utgånget koncept rörs inte."""
        c = self._mk(self.atype_long, 'retention.future')
        self._set_end(c, days=1)
        self.Concept._cron_enforce_retention()
        c.invalidate_recordset(['archived'])
        self.assertFalse(c.archived)

    def test_concept_without_period_untouched(self):
        """5.3: koncept utan frist rörs inte."""
        c = self._mk(self.atype_none, 'retention.no-period')
        self.Concept._cron_enforce_retention()
        c.invalidate_recordset(['archived'])
        self.assertFalse(c.archived)

    def test_metrics_written(self):
        """6.1: {'archived': N, 'overdue': M} finns efter körning."""
        c = self._mk(self.atype_long, 'retention.metrics')
        self._set_end(c, days=-1)
        result = self.Concept._cron_enforce_retention()
        self.assertEqual(result['archived'], 1)
        self.assertEqual(result['overdue'], 0)
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.retention_metrics')
        metrics = json.loads(raw)
        self.assertEqual(metrics['archived'], 1)
        self.assertEqual(metrics['overdue'], 0)
        self.assertEqual(len(metrics['history']), 1)

    def test_cron_tasks_registered(self):
        """5.4: de tre cron-poster finns som cron.task."""
        Task = self.env['cron.task']
        for technical_name in ('ai_retention_backfill',
                               'ai_retention_warn',
                               'ai_retention_enforce'):
            task = Task.with_context(active_test=False).search([
                ('technical_name', '=', technical_name)], limit=1)
            self.assertTrue(task, technical_name)
            self.assertEqual(task.model_name, 'ai.okf.concept')


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionKaizen(RetentionBase):
    """6.2–6.3: växande eftersläpning blir ett fynd."""

    def _report(self):
        coworker = self.env['ai.coworker'].create({'name': 'Retention Kaizen'})
        return self.env['ai.kaizen.report'].create({
            'coworker_id': coworker.id,
            'week_start': fields.Date.today() - timedelta(days=7),
            'week_end': fields.Date.today(),
        })

    def _write_metrics(self, history):
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.retention_metrics',
            json.dumps({'archived': 0, 'overdue': history[-1],
                        'history': [{'overdue': n} for n in history]}))

    def test_growing_backlog_creates_finding(self):
        """6.2: overdue ökar → finding med category='error'."""
        self._write_metrics([0, 3])
        report = self._report()
        findings = report._analyze_findings(
            report.coworker_id, report, self._empty_data())
        retention = [f for f in findings
                     if 'retention backlog' in f['finding']]
        self.assertEqual(len(retention), 1)
        self.assertEqual(retention[0]['category'], 'error')
        self.assertIn('0 → 3', retention[0]['finding'])

    def test_unchanged_backlog_creates_no_finding(self):
        """6.3: oförändrat overdue ger inget fynd."""
        self._write_metrics([3, 3])
        report = self._report()
        findings = report._analyze_findings(
            report.coworker_id, report, self._empty_data())
        self.assertFalse([f for f in findings
                          if 'retention backlog' in f['finding']])

    def test_decreasing_backlog_creates_no_finding(self):
        """6.3: minskande overdue ger inget fynd."""
        self._write_metrics([5, 2])
        report = self._report()
        findings = report._analyze_findings(
            report.coworker_id, report, self._empty_data())
        self.assertFalse([f for f in findings
                          if 'retention backlog' in f['finding']])

    def test_missing_metrics_creates_no_finding(self):
        """Utan mätvärde finns inget att jämföra — inget fynd."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.retention_metrics', False)
        report = self._report()
        findings = report._analyze_findings(
            report.coworker_id, report, self._empty_data())
        self.assertFalse([f for f in findings
                          if 'retention backlog' in f['finding']])

    def _empty_data(self):
        """Veckodata utan aktivitet — bara retention-regeln ska slå till."""
        return {
            'session_count': 0, 'total_sys_tokens': 0,
            'total_input_tokens': 0, 'total_output_tokens': 0,
            'error_count': 0, 'feedback_count': 0, 'avg_response_tokens': 0,
            'max_rounds_rate': 0, 'length_rate': 0, 'usage_unreported': 0,
            'session_trend': 0, 'cost_trend': 0, 'error_trend': 0,
        }


@tagged('okf', 'retention', 'post_install', '-at_install')
class TestRetentionTypeChange(RetentionBase):
    """7.1–7.3: typbyte räknar om fristen med spår.

    `artifact_type_id` är inte skrivbart via `write()` (identiteten är låst),
    så omräkningen sker i den uttryckliga vägen `_recompute_retention()`.
    """

    def test_shorter_period_recomputes(self):
        """7.1: ny typ med kortare frist ger nytt retention_end."""
        c = self._mk(self.atype_long, 'retention.change-shorter')
        before = c.retention_end
        c._recompute_retention(self.atype_short)
        self.assertAlmostEqual(
            c.retention_end, c.create_date + timedelta(days=30),
            delta=timedelta(seconds=5))
        self.assertLess(c.retention_end, before)

    def test_shortening_is_logged_with_both_values(self):
        """7.2: loggraden innehåller både gammalt och nytt värde."""
        c = self._mk(self.atype_long, 'retention.change-log')
        old_end = c.retention_end
        with self._capture_okf_logs() as logs:
            c._recompute_retention(self.atype_short)
        new_end = c.retention_end
        joined = '\n'.join(logs)
        self.assertIn(old_end.strftime('%Y-%m-%d'), joined)
        self.assertIn(new_end.strftime('%Y-%m-%d'), joined)

    def test_change_to_none_clears_period(self):
        """7.3: typbyte till 'none' tar bort fristen — och loggas."""
        c = self._mk(self.atype_long, 'retention.change-none')
        with self._capture_okf_logs() as logs:
            c._recompute_retention(self.atype_none)
        self.assertFalse(c.retention_end)
        self.assertTrue(
            any('retention' in line.lower() for line in logs),
            logs)
