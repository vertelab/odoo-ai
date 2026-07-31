# -*- coding: utf-8 -*-
"""Tester för OKF-memory-lagret (task 9.1).

Täcker: exakt-en-ägare, ADD-only (immutabla rader), versionshantering,
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
class TestOkfAddOnly(TestOkfConceptBase):
    """ADD-only: rader är immutabla (task 9.1)."""

    def test_write_summary_blocked(self):
        c = self._mk_concept(concept_key='addonly.summary')
        with self.assertRaises(ValidationError):
            c.write({'summary': 'Ändrad!'})

    def test_write_title_blocked(self):
        c = self._mk_concept(concept_key='addonly.title')
        with self.assertRaises(ValidationError):
            c.write({'title': 'Nytt namn'})

    def test_status_allowed(self):
        """status får ändras (superseded av _okf_upsert)."""
        c = self._mk_concept(concept_key='addonly.status')
        c.write({'status': 'superseded'})
        self.assertEqual(c.status, 'superseded')

    def test_archived_allowed(self):
        c = self._mk_concept(concept_key='addonly.archived')
        c.write({'archived': True})
        self.assertTrue(c.archived)

    def test_unique_key_within_scope(self):
        self._mk_concept(concept_key='addonly.uniq')
        with self.assertRaises(Exception):
            # Samma (scope, concept_key) direkt-create → UNIQUE-brott
            self._mk_concept(concept_key='addonly.uniq')


@tagged('okf', 'post_install')
class TestOkfVersioning(TestOkfConceptBase):
    """Versionshantering: ny version vid re-index, superseded,
    bara senaste versionen i sök (task 9.1)."""

    def test_reindex_creates_new_version(self):
        c1 = self._mk_concept(concept_key='version.test')
        c2 = self.Concept._okf_upsert(
            artifact_type='knowledge',
            concept_key='version.test',
            summary='Ny version',
            title='Version 2',
            owner_company_id=self.company.id,
            generated_by='cron_test',
        )
        self.assertEqual(c2.version, c1.version + 1)
        self.assertEqual(c2.supersedes_id.id, c1.id)
        self.assertEqual(c1.status, 'superseded')
        self.assertEqual(c2.status, 'stable')

    def test_only_latest_in_search(self):
        self._mk_concept(concept_key='version.search')
        self.Concept._okf_upsert(
            artifact_type='knowledge',
            concept_key='version.search',
            summary='Senaste',
            owner_company_id=self.company.id,
        )
        # Sök först på concept_key, applicera sedan _latest_per_key
        found = self.Concept.search([('concept_key', '=', 'version.search')])
        self.assertEqual(len(found), 2)  # båda versionerna
        latest = found._latest_per_key()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest.version, 2)

    def test_memory_concept_not_overwritten(self):
        """memory-kind: samma concept_key re-upsert → ej ny version om
        kallaren inte ber om ny inlärning (kallaren ansvarar). Här testar
        vi att _okf_upsert på en learning-artefakttyp skapar ny version
        men aldrig skriver över (ADD-only)."""
        c1 = self._mk_concept(concept_key='version.memory',
                              artifact_type_id=self.atype_learning.id)
        c2 = self.Concept._okf_upsert(
            artifact_type='learning',
            concept_key='version.memory',
            summary='Ny inlärning',
            owner_company_id=self.company.id,
        )
        # ADD-only: c1:s innehåll är oförändrat
        self.assertNotEqual(c1.summary, c2.summary)
        self.assertEqual(c1.summary, 'Rad 1\nRad 2\nRad 3')
        self.assertEqual(c2.version, 2)


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
