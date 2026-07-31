# -*- coding: utf-8 -*-
"""Odoo integration tests for the Workspace layer (tasks 2.x, 3.x).

Run via:  checkmodule -d <db> -m ai_agent_core -t
Covers:
- workspace.para.container (create, per-user search, suggest_areas HITL)
- workspace.para.ref (polymorphic ref to ai.okf.concept, revalidate)
- create_from_mail (task 3.2: partner find/create, eml attachment)
- action_place_in_para (task 3.3: inbox->PARA via ref, never a copy)
- action_nudge_para (task 3.4: one-time, knowledge -> resource auto)
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkspacePara(TransactionCase):

    def setUp(self):
        super().setUp()
        self.user = self.env.user
        self.Container = self.env['workspace.para.container']
        self.Ref = self.env['workspace.para.ref']
        self.Concept = self.env['ai.okf.concept']

    def _make_concept(self, title='Test concept', kind='knowledge'):
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', kind)], limit=1)
        if not atype:
            atype = self.env['ai.artifact.type'].create(
                {'name': kind, 'kind': kind})
        return self.Concept._okf_upsert(
            atype, 'test:%s' % title.lower().replace(' ', '_'), 'Summary',
            title=title, owner_user_id=self.user.id, status='draft')

    def test_01_create_container(self):
        c = self.Container.create({
            'name': 'My Project', 'kind': 'project',
        })
        self.assertEqual(c.user_id, self.user)
        self.assertEqual(c.state, 'active')
        self.assertEqual(c.ref_count, 0)

    def test_02_per_user_search(self):
        c = self.Container.create({'name': 'Mine', 'kind': 'project'})
        found = self.Container.search([('name', '=', 'Mine')])
        self.assertIn(c, found)

    def test_03_place_concept_in_para_via_ref(self):
        concept = self._make_concept('Invoice process')
        container = self.Container.create(
            {'name': 'Bookkeeping', 'kind': 'project'})
        ref = concept.action_place_in_para(container.id)
        self.assertEqual(ref.model, 'ai.okf.concept')
        self.assertEqual(ref.res_id, concept.id)
        self.assertEqual(ref.concept_id, concept.id)
        # Never a copy: the concept is unchanged (ADD-only)
        self.assertFalse(concept.in_inbox)  # placed -> no longer in inbox
        # Idempotent
        ref2 = concept.action_place_in_para(container.id)
        self.assertEqual(ref, ref2)

    def test_04_revalidate_drops_dead_refs(self):
        concept = self._make_concept('Temp')
        container = self.Container.create({'name': 'P', 'kind': 'project'})
        concept.action_place_in_para(container.id)
        rid = concept.id
        self.Concept.browse(rid).unlink()
        dead = container.ref_ids.revalidate()
        self.assertEqual(len(dead), 1)

    def test_05_suggest_areas_hitl(self):
        suggested = self.Container.suggest_areas(max_suggestions=3)
        for c in suggested:
            self.assertEqual(c.state, 'suggested')
            self.assertEqual(c.kind, 'area')
        # HITL: accept
        if suggested:
            suggested[0].action_accept_suggestion()
            self.assertEqual(suggested[0].state, 'active')
        # Idempotent: re-run does not duplicate
        again = self.Container.suggest_areas(max_suggestions=3)
        for c in suggested:
            dup = again.filtered(
                lambda x: x.name == c.name and x.kind == 'area')
            self.assertEqual(len(dup), 0 if c.state == 'active' else 1)

    def test_06_nudge_knowledge_auto_resource(self):
        concept = self._make_concept('Docs', kind='knowledge')
        result = concept.action_nudge_para()
        # knowledge -> auto placed in a resource container
        container = self.Ref.browse(result.id).container_id
        self.assertEqual(container.kind, 'resource')
        self.assertEqual(container.user_id, self.user)

    def test_07_nudge_one_time(self):
        concept = self._make_concept('Decision', kind='memory')
        activity = concept.action_nudge_para()
        if activity:  # only when an activity type exists
            activity2 = concept.action_nudge_para()
            self.assertEqual(activity, activity2)


@tagged('post_install', '-at_install')
class TestMailToConcept(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Concept = self.env['ai.okf.concept']

    def test_01_create_from_mail_creates_partner(self):
        Partner = self.env['res.partner']
        before = Partner.search_count([('email', '=ilike', 'ada@example.com')])
        concept = self.Concept.create_from_mail(
            subject='Hello',
            body='## Hello\n\nThis is a test email body.',
            from_email='ada@example.com',
            from_name='Ada Lovelace',
            user=self.env.user,
            source_ref='mail.message-id:test-123',
        )
        self.assertEqual(concept.title, 'Hello')
        self.assertIn('test-123', concept.concept_key)
        self.assertEqual(concept.owner_user_id, self.env.user)
        partner = Partner.search(
            [('email', '=ilike', 'ada@example.com')], limit=1)
        self.assertTrue(partner)
        self.assertFalse(partner.is_company)
        # In inbox until placed
        self.assertTrue(concept.in_inbox)

    def test_02_create_from_mail_eml_attachment(self):
        import base64
        eml = base64.b64encode(b'From: x@y.se\nSubject: Hej\n\nBody').decode()
        concept = self.Concept.create_from_mail(
            subject='Hej', body='Body', from_email='x@y.se',
            user=self.env.user, eml_data=eml)
        att = self.env['ir.attachment'].search([
            ('res_model', '=', 'ai.okf.concept'),
            ('res_id', '=', concept.id),
        ], limit=1)
        self.assertTrue(att)
        self.assertEqual(att.mimetype, 'message/rfc822')


@tagged('post_install', '-at_install')
class TestDistill(TransactionCase):
    """Task 4.1-4.4 — attribution + summarization layers."""

    def setUp(self):
        super().setUp()
        self.Concept = self.env['ai.okf.concept']
        self.Summary = self.env['executive.summary.interface']
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', 'knowledge')], limit=1)
        if not atype:
            atype = self.env['ai.artifact.type'].create(
                {'name': 'knowledge', 'kind': 'knowledge'})
        self.concept = self.Concept._okf_upsert(
            atype, 'test:distill', 'Första raden\nAndra raden',
            title='Distill test', owner_user_id=self.env.user.id,
            attribution=[
                {'line': 1, 'source_ref': 'res.partner,1'},
            ], status='draft')

    def test_01_attribution_html_clickable_and_uncertain(self):
        html = self.concept.render_attribution_html()
        self.assertIn('res.partner,1', html)          # klickbar källa
        self.assertIn('osäker', html)                  # rad 2 utan källa flaggas

    def test_02_distill_l2_l3_creates_rows(self):
        self.concept.distill_l2_l3(
            l2='Executive summary', l3='Synopsis', generated_by='test')
        l2 = self.Summary._latest(self.concept, 'L2')
        l3 = self.Summary._latest(self.concept, 'L3')
        self.assertTrue(l2)
        self.assertTrue(l3)
        self.assertEqual(l2.summary, 'Executive summary')
        self.assertEqual(l2.generated_by, 'test')

    def test_03_distill_batch_skips_existing(self):
        before = self.Summary.search_count([('concept_id', '=', self.concept.id)])
        self.assertEqual(before, 0)
        done1 = self.Concept._distill_inbox_batch(limit=50, generated_by='nightly')
        self.assertGreaterEqual(done1, 1)
        done2 = self.Concept._distill_inbox_batch(limit=50, generated_by='nightly')
        # Andra körningen ska inte duplicera L2/L3
        count = self.Summary.search_count(
            [('concept_id', '=', self.concept.id), ('level', '=', 'L2')])
        self.assertEqual(count, 1)

    def test_04_project_close_lessons_learned(self):
        Container = self.env['workspace.para.container']
        container = Container.create({'name': 'Proj A', 'kind': 'project'})
        ref = self.concept.action_place_in_para(container.id)
        self.assertTrue(ref)
        container.action_close_project()
        self.assertEqual(container.kind, 'archive')
        # Lessons-learned-koncept skapades med L2/L3
        lessons = self.Concept.search(
            [('concept_key', 'like', 'lessons:%%')], limit=1)
        self.assertTrue(lessons)
        l2 = self.Summary._latest(lessons, 'L2')
        self.assertTrue(l2)
        self.assertEqual(l2.generated_by, 'project_close')


@tagged('post_install', '-at_install')
class TestWorkspaceAgenda(TransactionCase):
    """Tasks 5.1-5.7 — agenda, GAP-förslag, HITL, snabbåtgärder."""

    def setUp(self):
        super().setUp()
        self.Suggestion = self.env['workspace.activity.suggestion']
        self.Gap = self.env['workspace.gap.engine']

    def test_01_create_suggestion_proposed(self):
        s = self.Suggestion._create_suggestion(
            'Boka uppföljning', suggestion_type='calendar.event',
            source='smart_deadline', user=self.env.user,
            diff_before={'progress': 40}, diff_after={'progress': 100})
        self.assertEqual(s.state, 'proposed')
        self.assertEqual(s.source, 'smart_deadline')

    def test_02_gap_engine_smart_deadline(self):
        Goal = self.env['ai.personal.goal']
        from datetime import date, timedelta
        goal = Goal.create({
            'name': 'Deadline snart', 'user_id': self.env.user.id,
            'status': 'active',
            'time_bound': date.today() + timedelta(days=3),
            'progress': 40.0,
        })
        suggs = self.Gap.suggest_for_user(user=self.env.user)
        smart = suggs.filtered(lambda s: s.source == 'smart_deadline')
        self.assertTrue(smart)
        self.assertEqual(smart.personal_goal_id, goal)
        self.assertEqual(smart.diff_after.get('progress'), 100.0)

    def test_03_hitl_accept_creates_activity(self):
        s = self.Suggestion._create_suggestion(
            'Skapa todo', suggestion_type='mail.activity',
            source='gap_okr', user=self.env.user)
        s.action_accept()
        self.assertEqual(s.state, 'accepted')
        self.assertTrue(s.result_ref)
        self.assertTrue(s.result_ref.startswith('mail.activity,'))

    def test_04_hitl_reject(self):
        s = self.Suggestion._create_suggestion(
            'Skapa todo 2', suggestion_type='mail.activity',
            source='coworker', user=self.env.user)
        s.action_reject()
        self.assertEqual(s.state, 'rejected')
        self.assertFalse(s.active)

    def test_05_why_view(self):
        s = self.Suggestion._create_suggestion(
            'Förslag med bevis', suggestion_type='mail.activity',
            source='coworker', user=self.env.user)
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', 'knowledge')], limit=1) or \
            self.env['ai.artifact.type'].create(
                {'name': 'knowledge', 'kind': 'knowledge'})
        concept = self.env['ai.okf.concept']._okf_upsert(
            atype, 'test:why', 'Bevis', title='Bevis',
            owner_user_id=self.env.user.id)
        s.evidence_ids = [(6, 0, [concept.id])]
        action = s.action_why()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        self.assertIn(concept.id, action['domain'][0][2])

    def test_06_build_agenda(self):
        agenda = self.Gap.build_agenda(user=self.env.user)
        self.assertIn('personal_goals', agenda)
        self.assertIn('meetings', agenda)
        self.assertIn('para_projects', agenda)
        self.assertIn('suggestions', agenda)
        self.assertIn('approvals', agenda)


@tagged('post_install', '-at_install')
class TestWorkspaceSafety(TransactionCase):
    """Tasks 8.1-8.4 — säkerhet, HITL-skrivskydd, idempotens."""

    def setUp(self):
        super().setUp()
        self.Suggestion = self.env['workspace.activity.suggestion']
        self.Container = self.env['workspace.para.container']
        self.Ref = self.env['workspace.para.ref']

    def test_01_no_write_without_approval(self):
        """8.2 HITL-skrivskydd: innan action_accept skrivs inga objekt."""
        s = self.Suggestion._create_suggestion(
            'Måste godkännas', suggestion_type='mail.activity',
            source='gap_okr', user=self.env.user)
        before = self.env['mail.activity'].search_count([
            ('summary', '=', 'Måste godkännas')])
        self.assertEqual(before, 0)
        # Först efter godkännande materialiseras
        s.action_accept()
        after = self.env['mail.activity'].search_count([
            ('summary', '=', 'Måste godkännas')])
        self.assertGreater(after, 0)

    def test_02_concept_rows_immutable(self):
        """8.4 ADD-only: konceptet får inte skrivas på innehållsfält."""
        from odoo.exceptions import ValidationError
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', 'knowledge')], limit=1)
        concept = self.env['ai.okf.concept']._okf_upsert(
            atype, 'test:immut', 'Original', title='Org',
            owner_user_id=self.env.user.id)
        with self.assertRaises(ValidationError):
            concept.write({'summary': 'Försök till ändring'})
        # Lifecycle-fält tillåts
        concept.write({'archived': True})
        self.assertTrue(concept.archived)

    def test_03_place_in_para_idempotent(self):
        """8.4 Idempotens: samma placering två gånger → samma ref."""
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', 'knowledge')], limit=1)
        concept = self.env['ai.okf.concept']._okf_upsert(
            atype, 'test:ido', 'Sum', title='Ido',
            owner_user_id=self.env.user.id)
        container = self.Container.create({'name': 'P', 'kind': 'project'})
        r1 = concept.action_place_in_para(container.id)
        r2 = concept.action_place_in_para(container.id)
        self.assertEqual(r1, r2)
        self.assertEqual(
            self.Ref.search_count([('concept_id', '=', concept.id)]), 1)

    def test_04_publish_company_keeps_attribution(self):
        """8.1 company-projection: publicera personligt→company behåller
        attribution (task 6.3/8.1)."""
        atype = self.env['ai.artifact.type'].search(
            [('kind', '=', 'knowledge')], limit=1)
        concept = self.env['ai.okf.concept']._okf_upsert(
            atype, 'test:pub', 'Sammanfattning', title='Pub',
            owner_user_id=self.env.user.id,
            attribution=[{'line': 1, 'source_ref': 'res.partner,1'}])
        company_concept = concept.action_publish_to_company()
        self.assertEqual(company_concept.scope, 'company')
        self.assertEqual(company_concept.attribution,
                         concept.attribution)
        # Idempotens: andra publiceringen returnerar samma
        again = concept.action_publish_to_company()
        self.assertEqual(again, company_concept)

    def test_05_core_manifest_clean(self):
        """10.7 ai_agent_core förblir core-rent (inga domänberoenden)."""
        import odoo
        manifest = odoo.modules.module.load_information_from_db(
            self.env.cr, 'ai_agent_core')
        deps = manifest.get('depends') or []
        core = {'base', 'mail', 'html_editor', 'hr', 'base_automation'}
        for d in deps:
            self.assertIn(d, core,
                          'ai_agent_core får inte bero på domänmodul: %s' % d)
