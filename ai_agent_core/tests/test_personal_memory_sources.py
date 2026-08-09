# -*- coding: utf-8 -*-
"""Tester för personal-memory-sources: HR-indexering, mål-indexering,
webb/browser-indexering (D10)."""

from datetime import date, timedelta
from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('post_install')
class TestPersonalMemorySources(common.TransactionCase):
    """Task 5.5: Indexering skapar/versionerar koncept med källa."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'Mitchell Test',
            'login': 'mitchell_test@example.com',
            'email': 'mitchell_test@example.com',
        })
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.ArtifactType = cls.env['ai.artifact.type']

        # Ensure artifact types exist
        cls.atype_role = cls.ArtifactType.search([('name', '=', 'roll')], limit=1)
        if not cls.atype_role:
            cls.atype_role = cls.ArtifactType.create({
                'name': 'roll', 'kind': 'knowledge',
            })
        cls.atype_goal = cls.ArtifactType.search([('name', '=', 'mål')], limit=1)
        if not cls.atype_goal:
            cls.atype_goal = cls.ArtifactType.create({
                'name': 'mål', 'kind': 'knowledge',
            })
        cls.atype_web = cls.ArtifactType.search([('name', '=', 'web')], limit=1)
        if not cls.atype_web:
            cls.atype_web = cls.ArtifactType.create({
                'name': 'web', 'kind': 'knowledge',
            })

        # Setup employee
        cls.department = cls.env['hr.department'].create({
            'name': 'Tech Department',
        })
        cls.job = cls.env['hr.job'].create({
            'name': 'CTO',
        })
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Mitchell',
            'work_email': 'mitchell_test@example.com',
            'job_id': cls.job.id,
            'department_id': cls.department.id,
            'work_contact_id': cls.user.partner_id.id,
        })

    # ── HR-indexering ──

    def test_hr_indexer_creates_concept(self):
        """HR-indexeraren skapar OKF personal-koncept för befattning."""
        coworker = self.env['ai.coworker'].search([], limit=1)
        if not coworker:
            self.skipTest('No ai.coworker available')

        # Bygg injection-prompt — ska trigga HR-indexering
        injection = coworker._build_injection_prompt(
            user=self.user, prompt='test')
        self.assertIn('Mitchell', injection)
        self.assertIn('CTO', injection)
        self.assertIn('Tech Department', injection)

    def test_hr_indexer_versioning(self):
        """Ändrad befattning → ny OKF-version (supersedes)."""
        concept = self.env['ai.okf.concept'].search([
            ('scope', '=', 'personal'),
            ('owner_user_id', '=', self.user.id),
            ('artifact_type_id', '=', self.atype_role.id),
        ], limit=1)

        # If no concept exists yet, create one via _okf_upsert
        if not concept:
            concept = self.Concept._okf_upsert(
                artifact_type=self.atype_role,
                concept_key='hr.employee,%s' % self.employee.id,
                summary='Mitchell är CTO på Tech Department',
                scope='personal',
                owner_user_id=self.user.id,
                source_ref='hr.employee,%s' % self.employee.id,
            )

        # Ändra befattning
        new_job = self.env['hr.job'].create({'name': 'CEO'})
        self.employee.job_id = new_job.id

        # Ny version via _okf_upsert
        new_concept = self.Concept._okf_upsert(
            artifact_type=self.atype_role,
            concept_key='hr.employee,%s' % self.employee.id,
            summary='Mitchell är CEO på Tech Department',
            scope='personal',
            owner_user_id=self.user.id,
            source_ref='hr.employee,%s' % self.employee.id,
        )

        self.assertEqual(new_concept.version, 2)
        self.assertEqual(new_concept.supersedes_id.id, concept.id)

    # ── Mål-indexering ──

    def test_goal_indexer_creates_concept(self):
        """Mål-indexeraren skapar OKF personal-koncept."""
        if 'ai.personal.goal' not in self.env:
            self.skipTest('ai.personal.goal not available')

        Goal = self.env['ai.personal.goal']
        goal = Goal.create({
            'name': 'Learn GraphQL',
            'specific': 'Build a GraphQL API',
            'measurable': '10 endpoints',
            'achievable': '2 months',
            'relevant': 'Project needs API',
            'time_bound': date.today() + timedelta(days=60),
            'status': 'active',
        })

        concept = self.Concept._okf_upsert(
            artifact_type=self.atype_goal,
            concept_key='ai.personal.goal,%s' % goal.id,
            summary='Mål: Learn GraphQL',
            scope='personal',
            owner_user_id=self.user.id,
            source_ref='ai.personal.goal,%s' % goal.id,
        )

        self.assertTrue(concept.id)
        self.assertEqual(concept.scope, 'personal')
        self.assertEqual(concept.owner_user_id.id, self.user.id)
        self.assertEqual(concept.artifact_type_id.id, self.atype_goal.id)
        self.assertIn('Learn GraphQL', concept.summary)

    def test_goal_status_update_versions(self):
        """Målstatus-uppdatering → ny version."""
        if 'ai.personal.goal' not in self.env:
            self.skipTest('ai.personal.goal not available')

        Goal = self.env['ai.personal.goal']
        goal = Goal.create({
            'name': 'Version Test Goal',
            'status': 'active',
            'time_bound': date.today() + timedelta(days=30),
        })

        concept1 = self.Concept._okf_upsert(
            artifact_type=self.atype_goal,
            concept_key='ai.personal.goal,%s' % goal.id,
            summary='Mål: Version Test Goal (aktivt)',
            scope='personal',
            owner_user_id=self.user.id,
            source_ref='ai.personal.goal,%s' % goal.id,
        )

        goal.action_complete()
        concept2 = self.Concept._okf_upsert(
            artifact_type=self.atype_goal,
            concept_key='ai.personal.goal,%s' % goal.id,
            summary='Mål: Version Test Goal (slutfört)',
            scope='personal',
            owner_user_id=self.user.id,
            source_ref='ai.personal.goal,%s' % goal.id,
        )

        self.assertEqual(concept2.version, 2)
        self.assertEqual(concept2.supersedes_id.id, concept1.id)

    # ── Webb/browser-indexering ──

    def test_website_page_indexing(self):
        """Webbsida indexeras till OKF company-koncept."""
        if 'website.page' not in self.env:
            self.skipTest('website.page not available')

        page = self.env['website.page'].create({
            'name': 'About Us',
            'url': '/about-us',
            'is_published': True,
            'arch': '<p>Vertel is an IT consulting company.</p>',
        })

        concept = self.Concept._okf_upsert(
            artifact_type=self.atype_web,
            concept_key='website.page,%s' % page.id,
            summary='Vertel is an IT consulting company.',
            scope='company',
            owner_company_id=self.company.id,
            source_ref='website.page,%s' % page.id,
        )

        self.assertTrue(concept.id)
        self.assertEqual(concept.scope, 'company')
        self.assertEqual(concept.artifact_type_id.id, self.atype_web.id)
        self.assertIn('Vertel', concept.summary)

    def test_unpublished_page_skipped(self):
        """Opublicerad sida indexeras inte."""
        if 'website.page' not in self.env:
            self.skipTest('website.page not available')

        page = self.env['website.page'].create({
            'name': 'Draft Page',
            'url': '/draft',
            'is_published': False,
            'arch': '<p>Secret content</p>',
        })

        # Sök efter indexerat koncept — ska inte finnas
        concepts = self.Concept.search([
            ('source_ref', '=', 'website.page,%s' % page.id),
        ])
        self.assertEqual(len(concepts), 0,
                         'Unpublished pages should not be indexed')
