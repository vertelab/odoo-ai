# -*- coding: utf-8 -*-
"""Tests för org-goal-integrity — ägarkedja, kaskad, heartbeat, ansiktslistor.

Täcker:
  3.2/3.3  onboarding-kaskaden (parent_id mot company-målet, aldrig föräldralös)
  4.2/4.3  heartbeat målval (prioritetsordnad ägarkedja, D4)
  6.2      AI-chefens utförarval (department_coworker_ids, D7)
  7.3      avdelningens ansiktslistor (employee_ids vs ai_coworker_ids, D9)
"""

from odoo.tests import common, tagged


@tagged('-at_install', 'post_install')
class TestOrgGoalIntegrity(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.dept_a = cls.env['hr.department'].create({
            'name': 'Test Dept A',
            'company_id': cls.company.id,
        })
        cls.dept_b = cls.env['hr.department'].create({
            'name': 'Test Dept B',
            'company_id': cls.company.id,
        })
        cls.coworker_a = cls.env['ai.coworker'].create({
            'name': 'Test Coworker A',
            'department_id': cls.dept_a.id,
            'status': 'active',
        })
        cls.coworker_b = cls.env['ai.coworker'].create({
            'name': 'Test Coworker B',
            'department_id': cls.dept_a.id,
            'status': 'active',
        })
        cls.coworker_c = cls.env['ai.coworker'].create({
            'name': 'Test Coworker C',
            'department_id': cls.dept_b.id,
            'status': 'active',
        })

    # ════════════════════════════════════════════
    # 3.2/3.3 — Onboarding-kaskaden
    # ════════════════════════════════════════════

    def _make_onboarding(self, proposal=None):
        return self.env['ai.onboarding'].create({
            'company_id': self.company.id,
            'ceo_user_id': self.env.user.id,
            'proposal_json': proposal or {},
        })

    def test_create_department_goal_sets_parent_company(self):
        """3.2: department-mål får parent_id mot ett company-nivå-mål."""
        onboarding = self._make_onboarding()
        goal = onboarding._create_goal(
            {'name': 'Dept Goal', 'key_results': []},
            level='department', department=self.dept_a)
        self.assertTrue(goal)
        self.assertTrue(goal.parent_id, 'department-mål saknar parent_id')
        self.assertEqual(goal.parent_id.level, 'company')

    def test_department_goal_without_company_goal_creates_one(self):
        """3.3: utan befintligt company-mål skapas det först — aldrig föräldralöst."""
        Goal = self.env['ai.org.goal']
        Goal.search([('level', '=', 'company'),
                     ('company_id', '=', self.company.id)]).unlink()
        self.assertFalse(Goal.search([
            ('level', '=', 'company'), ('company_id', '=', self.company.id)]))

        onboarding = self._make_onboarding()
        goal = onboarding._create_goal(
            {'name': 'Dept Goal 2', 'key_results': []},
            level='department', department=self.dept_a)

        self.assertTrue(goal.parent_id)
        self.assertEqual(goal.parent_id.level, 'company')
        # Company-målet skapades i samma svep.
        self.assertEqual(Goal.search_count([
            ('level', '=', 'company'), ('company_id', '=', self.company.id)]), 1)

    # ════════════════════════════════════════════
    # 4.2/4.3 — Heartbeat målval (D4)
    # ════════════════════════════════════════════

    def _goal(self, name, **vals):
        vals.setdefault('status', 'active')
        vals.setdefault('company_id', self.company.id)
        vals.setdefault('level', 'coworker')
        return self.env['ai.org.goal'].create(dict(name=name, **vals))

    def test_heartbeat_single_owner(self):
        """4.2: coworkern plockar upp ett mål den äger (coworker_id)."""
        goal = self._goal('Owned', coworker_id=self.coworker_a.id)
        found = self.coworker_a._find_heartbeat_goal()
        self.assertEqual(found, goal)

    def test_heartbeat_team_member(self):
        """4.2: coworkern plockar upp ett teammål (coworker_ids)."""
        goal = self._goal('Team', coworker_ids=[(6, 0, [self.coworker_a.id])])
        found = self.coworker_a._find_heartbeat_goal()
        self.assertEqual(found, goal)

    def test_heartbeat_ownerless_department_goal(self):
        """4.2: coworkern plockar upp ett ägarlöst avdelningsmål."""
        goal = self._goal('Ownerless', department_id=self.dept_a.id)
        found = self.coworker_a._find_heartbeat_goal()
        self.assertEqual(found, goal)

    def test_heartbeat_ignores_other_department(self):
        """4.2: ägarlöst mål i annan avdelning köas inte."""
        self._goal('Other Dept', department_id=self.dept_b.id)
        found = self.coworker_a._find_heartbeat_goal()
        self.assertFalse(found)

    def test_heartbeat_direct_ownership_prioritized(self):
        """4.2: direkt ägarskap prioriteras före ägarlösa avdelningsmål."""
        own = self._goal('Own Priority', coworker_id=self.coworker_a.id)
        self._goal('Ownerless Priority', department_id=self.dept_a.id)
        found = self.coworker_a._find_heartbeat_goal()
        self.assertEqual(found, own)

    def test_heartbeat_two_coworkers_share_ownerless_goal(self):
        """4.3: två coworkers i samma avdelning kan båda välja samma ägarlösa mål."""
        goal = self._goal('Shared', department_id=self.dept_a.id)
        self.assertEqual(self.coworker_a._find_heartbeat_goal(), goal)
        self.assertEqual(self.coworker_b._find_heartbeat_goal(), goal)

    # ════════════════════════════════════════════
    # 6.2 — AI-chefens utförarval (D7)
    # ════════════════════════════════════════════

    def test_department_coworker_ids_includes_manager_and_staff(self):
        """6.2: utförarvalet = avdelningens coworkers + chefen själv."""
        manager = self.env['ai.coworker'].create({
            'name': 'Test Manager',
            'department_id': self.dept_a.id,
            'status': 'active',
        })
        self.dept_a.ai_manager_id = manager.id
        # department_id på ai.org.task är compute (goal_id.department_id eller
        # job_id.department_id) — sätt avdelningen via ett mål, inte direkt.
        goal = self._goal('Dept Goal', department_id=self.dept_a.id)
        task = self.env['ai.org.task'].create({
            'name': 'Executor Selection',
            'goal_id': goal.id,
        })
        self.assertEqual(task.department_id, self.dept_a)
        candidates = task.department_coworker_ids
        self.assertIn(self.coworker_a, candidates)
        self.assertIn(self.coworker_b, candidates)
        self.assertIn(manager, candidates)

    # ════════════════════════════════════════════
    # 7.3 — Avdelningens ansiktslistor (D9)
    # ════════════════════════════════════════════

    def test_employee_ids_excludes_ai(self):
        """7.3: employee_ids = mänskliga (is_ai=False); ai_coworker_ids = AI."""
        human = self.env['hr.employee'].create({
            'name': 'Human A',
            'department_id': self.dept_a.id,
            'is_ai': False,
        })
        ai_emp = self.env['hr.employee'].create({
            'name': 'AI A',
            'department_id': self.dept_a.id,
            'is_ai': True,
        })
        self.dept_a.invalidate_recordset(['employee_ids'])
        self.assertIn(human, self.dept_a.employee_ids)
        self.assertNotIn(ai_emp, self.dept_a.employee_ids)
        self.assertIn(self.coworker_a, self.dept_a.ai_coworker_ids)
