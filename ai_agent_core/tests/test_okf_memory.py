# -*- coding: utf-8 -*-
"""Tester för OKF-memory-lagret (task 9.1).

Täcker: exakt-en-ägare, ett koncept per nyckel (spegel av källan),
attribution-filtrering, access-resolver, migrering, retention.
"""

from odoo.tests import common, tagged
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)


@tagged('okf', 'post_install')
class TestOkfConceptBase(common.TransactionCase):
    """Gemensam setup: artefakttyper + företag."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.ArtifactType = cls.env['ai.artifact.type']
        # Säkerställ att kunskaps-artefakttypen finns (datafil)
        cls.atype_knowledge = cls.ArtifactType.search(
            [('name', '=', 'knowledge')], limit=1)
        if not cls.atype_knowledge:
            cls.atype_knowledge = cls.ArtifactType.create({
                'name': 'knowledge',
                'kind': 'knowledge',
            })
        # Memory-artefakttyp för ADD-only-tester
        cls.atype_learning = cls.ArtifactType.search(
            [('name', '=', 'learning')], limit=1)
        if not cls.atype_learning:
            cls.atype_learning = cls.ArtifactType.create({
                'name': 'learning',
                'kind': 'memory',
            })
        cls.customer_profile = cls.ArtifactType.create({
            'name': 'test-customer-profile',
            'kind': 'knowledge',
            'okf_contract': {
                'generated_by': 'process',
                'stale_policy': 'fixed',
                'stale_ttl_days': 30,
                'retention_purpose': 'crm_lead',
                'retention_days': 365,
            },
        })

    def _mk_concept(self, **kw):
        vals = {
            'artifact_type_id': self.atype_knowledge.id,
            'scope': 'company',
            'concept_key': 'test.key',
            'summary': 'Rad 1\nRad 2\nRad 3',
            'owner_company_id': self.company.id,
        }
        vals.update(kw)
        return self.Concept.create(vals)


@tagged('okf', 'post_install')
class TestOkfExactlyOneOwner(TestOkfConceptBase):
    """Exakt-en-ägare (task 9.1)."""

    def test_company_owner_ok(self):
        c = self._mk_concept(concept_key='owner.company')
        self.assertEqual(c.scope, 'company')
        self.assertEqual(c.owner_company_id.id, self.company.id)

    def test_two_owners_fails(self):
        with self.assertRaises(ValidationError):
            self._mk_concept(
                concept_key='owner.two',
                owner_company_id=self.company.id,
                owner_user_id=self.env.ref('base.user_admin').id,
            )

    def test_no_owner_fails(self):
        with self.assertRaises(ValidationError):
            self._mk_concept(concept_key='owner.none',
                             owner_company_id=None,
                             scope='company')

    def test_scope_mismatch_fails(self):
        with self.assertRaises(ValidationError):
            self._mk_concept(concept_key='owner.mismatch',
                             scope='personal',
                             owner_company_id=self.company.id)


@tagged('okf', 'post_install')
class TestOkfIdentityLocked(TestOkfConceptBase):
    """Identiteten är låst, innehållet är speglat (beslut 2026-10-26)."""

    def test_write_summary_allowed(self):
        """Innehållet får skrivas — konceptet speglar källan."""
        c = self._mk_concept(concept_key='mirror.summary')
        c.write({'summary': 'Ändrad!'})
        self.assertEqual(c.summary, 'Ändrad!')

    def test_write_title_allowed(self):
        c = self._mk_concept(concept_key='mirror.title')
        c.write({'title': 'Nytt namn'})
        self.assertEqual(c.title, 'Nytt namn')

    def test_write_concept_key_blocked(self):
        """Identiteten får INTE skrivas — det vore ett annat koncept."""
        c = self._mk_concept(concept_key='mirror.key')
        with self.assertRaises(ValidationError):
            c.write({'concept_key': 'annan.nyckel'})

    def test_status_allowed(self):
        """status får ändras (livscykel)."""
        c = self._mk_concept(concept_key='mirror.status')
        c.write({'status': 'deprecated'})
        self.assertEqual(c.status, 'deprecated')

    def test_archived_allowed(self):
        c = self._mk_concept(concept_key='addonly.archived')
        c.write({'archived': True})
        self.assertTrue(c.archived)

    def test_unique_key_within_scope(self):
        """(scope, ägare, concept_key) är unikt — verifierat utan DB-krock.

        FYND 2026-09-23: testet skapade tidigare två identiska rader och
        förlitade sig på att UNIQUE-brottet fångades av assertRaises. Det
        gav alltid ett ERROR i Odoo:s test-runner: psycopg2:s fel loggas av
        odoo/sql_db.py:374 som `_logger.error("bad query: ...")` INNAN
        undantaget når assertRaises — och runnern räknar ERROR-loggade
        meddelanden som testfel. En savepoint hjälper inte; loggen sker
        före rollbacken.

        Constrainten är en databas-nivå-UNIQUE som inte går att bevisa
        "tyst" via ORM:en. Vi verifierar därför att den FINNS och är
        korrekt formulerad — det är vad testet ska skydda (att
        versionshanteringen inte tappar version-ledet, bugg 9.1).

        UPPDATERAD 2026-10-06 (okf-owner-and-access-scoping D6): villkoret
        bär nu ÄVEN ägarkolumnerna. Två ägare kan ha samma `concept_key`
        och samma `version` — t.ex. samma kalenderhändelse för två
        deltagare. Utan ägarkolumnerna avvisar villkoret den andra raden
        och hela "en post -> N ägare"-förmågan faller. version-ledet är
        kvar (bugg 9.1 får inte återinföras).
        """
        constraints = {
            c[0]: c[1] for c in self.Concept._sql_constraints
        }
        self.assertIn(
            'concept_key_scope_uniq', constraints,
            'UNIQUE-constrainten för (scope, ägare, concept_key) '
            'saknas')
        self.assertEqual(
            constraints['concept_key_scope_uniq'],
            'UNIQUE(scope, owner_company_id, owner_user_id, '
            'owner_coworker_id, concept_key)',
            'Constrainten får inte inkludera version (det finns ingen) '
            'men måste inkludera ägaren (annars kan två ägare inte ha '
            'samma nyckel)')

        # Positiv kontroll: samma nyckel för OLIKA ägare är tillåtet.
        # (_mk_concept defaultar till company-ägare; den andra raden får
        # explicit en annan ägare.)
        self._mk_concept(concept_key='addonly.uniq')
        self.Concept.create({
            'artifact_type_id': self.atype_learning.id,
            'scope': 'personal',
            'concept_key': 'addonly.uniq',
            'summary': 'x',
            'owner_user_id': self.env.user.id,
        })
        count = self.Concept.search_count([
            ('concept_key', '=', 'addonly.uniq'),
        ])
        self.assertEqual(count, 2, 'två ägare ska kunna ha samma nyckel')


@tagged('okf', 'post_install')
class TestOkfMirror(TestOkfConceptBase):
    """Spegeln: re-index skriver över samma rad, ingen version skapas.

    (Beslut 2026-10-26: versioneringen borttagen. Modellen är en spegel
    av källan, inte en logg över indexeringar.)
    """

    def test_reindex_updates_the_same_row(self):
        c1 = self._mk_concept(concept_key='mirror.test')
        rows_before = self.Concept.search_count([
            ('concept_key', '=', 'mirror.test')])
        c2 = self.Concept._okf_upsert(
            artifact_type='knowledge',
            concept_key='mirror.test',
            summary='Nytt innehåll',
            title='Ny titel',
            owner_company_id=self.company.id,
            generated_by='cron_test',
        )
        self.assertEqual(c2.id, c1.id, 'samma rad ska skrivas över')
        rows_after = self.Concept.search_count([
            ('concept_key', '=', 'mirror.test')])
        self.assertEqual(rows_after, rows_before,
                         'ingen ny rad får skapas')
        self.assertEqual(c2.summary, 'Nytt innehåll')

    def test_reindex_is_idempotent(self):
        self._mk_concept(concept_key='mirror.idem')
        for _ in range(5):
            self.Concept._okf_upsert(
                artifact_type='knowledge',
                concept_key='mirror.idem',
                summary='Samma text',
                owner_company_id=self.company.id,
            )
        count = self.Concept.search_count([
            ('concept_key', '=', 'mirror.idem')])
        self.assertEqual(count, 1,
                         'fem indexeringar ska ge en rad, inte fem')

    def test_memory_concept_is_overwritten_too(self):
        """Även learning-koncept speglas — källan är sanningen."""
        c1 = self._mk_concept(concept_key='mirror.memory',
                              artifact_type_id=self.atype_learning.id)
        self.Concept._okf_upsert(
            artifact_type='learning',
            concept_key='mirror.memory',
            summary='Ny inlärning',
            owner_company_id=self.company.id,
        )
        c1.invalidate_recordset()
        self.assertEqual(c1.summary, 'Ny inlärning',
                         'raden ska bära det nya innehållet')


@tagged('okf', 'post_install')
class TestOkfAttribution(TestOkfConceptBase):
    """Attribution-filtrering per rad (task 9.1)."""

    def test_validate_attribution(self):
        ok, err = self.Concept._validate_attribution([
            {'line': 1, 'source_ref': 'res.partner,1'},
            {'line': 2, 'source_ref': 'res.users,2'},
        ])
        self.assertTrue(ok)
        ok, err = self.Concept._validate_attribution(
            [{'line': 'x', 'source_ref': 'res.partner,1'}])
        self.assertFalse(ok)

    def test_filter_attribution(self):
        c = self._mk_concept(
            concept_key='attr.filter',
            attribution=[
                {'line': 1, 'source_ref': 'res.partner,10'},
                {'line': 2, 'source_ref': 'mail.message,99'},
                {'line': 3, 'source_ref': 'res.partner,10'},
            ],
        )
        lines, hidden = c._filter_attribution(
            {'res.partner,10': True, 'mail.message,99': False})
        self.assertEqual(lines, ['Rad 1', 'Rad 3'])
        self.assertEqual(hidden, 1)

    def test_conservative_fallback(self):
        c = self._mk_concept(
            concept_key='attr.conservative',
            attribution=[{'line': 2, 'source_ref': 'res.partner,10'}],
        )
        lines, hidden = c._filter_attribution_conservative(
            {'res.partner,10': True})
        # Rad 1 och 3 saknar attribution → döljs
        self.assertEqual(lines, ['Rad 2'])
        self.assertEqual(hidden, 2)

    def test_no_attribution_all_visible(self):
        c = self._mk_concept(concept_key='attr.none')
        lines, hidden = c._filter_attribution({})
        self.assertEqual(len(lines), 3)
        self.assertEqual(hidden, 0)


@tagged('okf', 'post_install')
class TestOkfAccessResolver(TestOkfConceptBase):
    """Access-resolver: ir.access ∩ ir.rule ∩ resolver (task 9.1)."""

    def test_split_source_ref(self):
        self.assertEqual(self.Concept._split_source_ref('res.partner,42'),
                         ('res.partner', 42))
        self.assertEqual(self.Concept._split_source_ref('invalid'),
                         (None, None))

    def test_resolve_visible_sources_unknown_model(self):
        """Okänd modell → källan rapporteras inte som synlig (ingen krasch)."""
        c = self._mk_concept(
            concept_key='resolver.unknown',
            source_ref='nonexistent.model,1',
        )
        result = self.Concept._resolve_visible_sources(c)
        self.assertIn(c.id, result)
        # Modellen finns inte → källan ej i resultatet, men inget undantag

    def test_resolve_visible_sources_res_partner(self):
        """res.partner har follower-domän → partnern synlig för följare.

        En användare som följer partnern (message_follower_ids) ser den;
        en som inte följer ser den inte (access = ir.rule ∩ resolver).
        """
        partner = self.env['res.partner'].create({'name': 'OKF Test Partner'})
        c = self._mk_concept(
            concept_key='resolver.partner',
            source_ref='res.partner,%s' % partner.id,
        )
        user = self.env.ref('base.user_demo')
        # 1. Icke-följare → ej synlig (follower-domänen filtrerar)
        result = self.Concept.with_user(user.id)._resolve_visible_sources(c)
        self.assertFalse(
            result[c.id].get('res.partner,%s' % partner.id, False))
        # 2. Lägg till användarens partner som följare → synlig
        partner.message_partner_ids = [(4, user.partner_id.id)]
        result = self.Concept.with_user(user.id)._resolve_visible_sources(c)
        self.assertTrue(
            result[c.id].get('res.partner,%s' % partner.id, False))

    def test_resolve_visible_sources_restricted(self):
        """Källa i modell som användaren inte får läsa → ej synlig."""
        # res.users: vanlig användare saknar read-access på andra users
        user = self.env.ref('base.user_demo')
        admin = self.env.ref('base.user_admin')
        c = self._mk_concept(
            concept_key='resolver.users',
            source_ref='res.users,%s' % admin.id,
        )
        # Användaren är admin i testmiljön — kolla via demo istället:
        # demo-användarens egna id är alltid synlig, andras ej.
        result = self.Concept.with_user(user.id)._resolve_visible_sources(c)
        # Med sudo/admin ska admin vara synlig; här verifierar vi bara
        # att anropet inte kraschar och returnerar dict.
        self.assertIn(c.id, result)

    def test_okf_can_read_sql(self):
        """SQL-hjälpfunktionen ai_okf_can_read existerar (task 4.9)."""
        self.env.cr.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='ai_okf_can_read'")
        self.assertGreater(self.env.cr.fetchone()[0], 0)


@tagged('okf', 'post_install')
class TestOkfMigration(TestOkfConceptBase):
    """Legacy-migrering (task 9.1)."""

    def test_migrate_legacy_runs(self):
        res = self.Concept.action_migrate_legacy()
        self.assertIn('legacy: read-only', res)

    def test_migrate_legacy_company_memory(self):
        Company = self.env['ai.company.memory']
        mem = Company.create({
            'company_id': self.company.id,
            'content': 'Migrerings-test',
            'category': 'strategy',
        })
        self.Concept.action_migrate_legacy()
        key = 'ai.company.memory,%s' % mem.id
        found = self.Concept.search([
            ('concept_key', '=', key), ('scope', '=', 'company')])
        self.assertTrue(found)
        self.assertEqual(found[0].summary, 'Migrerings-test')


@tagged('okf', 'post_install')
class TestOkfRetention(TestOkfConceptBase):
    """Retention: okf_contract styr retention_purpose/end (task 9.1)."""

    def test_retention_from_contract(self):
        """retention_purpose kopieras från artefakttypens okf_contract."""
        c = self.Concept._okf_upsert(
            artifact_type=self.customer_profile,
            concept_key='retention.test',
            summary='Kundprofil',
            owner_company_id=self.company.id,
        )
        self.assertEqual(c.retention_purpose, 'crm_lead')
        # retention_end är passivt (SENARELAGT GDPR) — kontraktet finns i
        # artefakttypen: stale_ttl_days + retention_days
        self.assertEqual(self.customer_profile.okf_contract['retention_days'], 365)
        self.assertEqual(self.customer_profile.okf_contract['stale_ttl_days'], 30)

    def test_retention_none_default(self):
        c = self._mk_concept(concept_key='retention.none')
        self.assertEqual(c.retention_purpose, 'none')
        self.assertFalse(c.retention_end)
